"""One running SUMO simulation, driven step by step over TraCI."""

import itertools
import json
import re
import shutil
import tempfile
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sumolib
import traci
import traci.constants as tc

from .config import AREAS_DIR
from .demand import DemandGenerator, DemandSettings, EdgePools
from .geo import NetProjection
from .vtypes import VEHICLE_TYPES, vclass_for, vtypes_xml

VTYPE_INDEX = {vt.id: i for i, vt in enumerate(VEHICLE_TYPES)}
VTYPE_MAX_SPEED = [vt.max_speed for vt in VEHICLE_TYPES]

VEHICLE_VARS = [tc.VAR_POSITION, tc.VAR_ANGLE, tc.VAR_SPEED, tc.VAR_ROAD_ID, tc.VAR_TIMELOSS]

STEP_LENGTH = 1.0  # seconds of simulated time per step
EDGE_UPDATE_EVERY = 5  # steps between road congestion updates
STATS_WINDOW = 600  # seconds; rolling window for travel time / throughput
MAX_PENDING = 3000  # stop injecting when this many vehicles wait to enter
IGNORE_JUNCTION_BLOCKER = 30  # s a vehicle may stand in a junction before others drive past it
# Vehicle data is fetched through one context subscription around a junction
# with a radius covering any network, switched on only for steps that need it.
# Per-vehicle subscriptions would be decoded every step, which dominates the
# run time once there are tens of thousands of vehicles (whole-city runs).
CONTEXT_RANGE = 1e6  # m

# traci.start() touches module-level state; serialise it across threads.
_start_lock = threading.Lock()
_labels = itertools.count()


@dataclass
class SimOptions:
    sublane: bool = True  # lane-free Dhaka-style filtering (slower to simulate)
    teleport_after: int = 300  # s a vehicle may be stuck before SUMO removes it; -1 = never
    # Battery and pedal rickshaws may use trunk and primary roads. The official
    # rule bans them there, but in practice they are everywhere (2025–26).
    rickshaws_on_main_roads: bool = True
    start_hour: float = 8.0  # clock shown in the UI
    seed: int = 42


class Area:
    """A prepared area on disk (see prepare.py). Loaded once and shared."""

    def __init__(self, area_dir: Path):
        self.dir = area_dir
        self.meta = json.loads((area_dir / "area.json").read_text())
        self.net_file = area_dir / "net.net.xml"
        self.net = sumolib.net.readNet(str(self.net_file), withInternal=False)
        self.projection = NetProjection.from_net_file(self.net_file)
        self.edge_speed = {e.getID(): e.getSpeed() for e in self.net.getEdges()}
        west, south, east, north = self.meta["bbox"]
        self.bbox_xy = (
            self.net.convertLonLat2XY(west, south),
            self.net.convertLonLat2XY(east, north),
        )
        self._pools: dict[str, EdgePools | None] = {}
        self._pools_lock = threading.Lock()

    def pools(self, vclass: str) -> EdgePools | None:
        """Where vehicles of this class can start and end trips (cached)."""
        with self._pools_lock:
            if vclass not in self._pools:
                self._pools[vclass] = EdgePools.build(self.net, vclass, self.bbox_xy)
            return self._pools[vclass]

    @property
    def id(self) -> str:
        return self.meta["id"]


_area_cache: dict[str, Area] = {}
_area_lock = threading.Lock()  # the city network takes seconds to load; load it once


def list_areas() -> list[dict]:
    if not AREAS_DIR.exists():
        return []
    return [
        json.loads((d / "area.json").read_text())
        for d in sorted(AREAS_DIR.iterdir())
        if (d / "area.json").exists() and (d / "net.net.xml").exists()
    ]


def load_area(area_id: str) -> Area:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", area_id or ""):
        raise KeyError(area_id)
    with _area_lock:
        if area_id not in _area_cache:
            area_dir = AREAS_DIR / area_id
            if not (area_dir / "area.json").exists():
                raise KeyError(area_id)
            _area_cache[area_id] = Area(area_dir)
        return _area_cache[area_id]


class Simulation:
    def __init__(self, area: Area, demand: DemandSettings, options: SimOptions):
        self.area = area
        self.demand = demand
        self.options = options
        self.generator = DemandGenerator(
            {vt.id: area.pools(vclass_for(vt, options.rickshaws_on_main_roads)) for vt in VEHICLE_TYPES},
            seed=options.seed,
        )
        self.conn: traci.connection.Connection | None = None
        self._tmp = Path(tempfile.mkdtemp(prefix="traffic-sim-"))
        self._next_id = 0
        self.vtype_of: dict[str, int] = {}
        self.depart_time: dict[str, float] = {}
        self.last_timeloss: dict[str, float] = {}
        self.edge_level: dict[str, float] = {}  # smoothed speed ratio, 0..1
        self.recent_trips: deque[tuple[float, float, float]] = deque()  # (arrival, duration, delay)
        self.total_departed = 0
        self.total_arrived = 0
        self.total_teleports = 0
        self.failed_routes = 0
        self.step_count = 0
        self.running = 0
        self._edges_dirty = False
        self._context_junction = next(iter(area.net.getNodes())).getID()

    # --- lifecycle -------------------------------------------------------

    def start(self) -> None:
        vtypes_file = self._tmp / "vtypes.add.xml"
        vtypes_file.write_text(vtypes_xml(self.options.rickshaws_on_main_roads))
        o = self.options
        cmd = [
            "sumo",
            "-n", str(self.area.net_file),
            "--additional-files", str(vtypes_file),
            "--step-length", str(STEP_LENGTH),
            "--time-to-teleport", str(o.teleport_after),
            # Drivers squeeze past a vehicle stalled in the junction box.
            "--ignore-junction-blocker", str(IGNORE_JUNCTION_BLOCKER),
            "--max-depart-delay", "600",
            "--collision.action", "warn",
            "--collision.check-junctions", "false",
            "--ignore-route-errors",
            "--seed", str(o.seed),
            "--no-step-log",
            "--no-warnings",
            "--duration-log.disable",
        ]
        if o.sublane:
            cmd += ["--lateral-resolution", "0.8"]
        label = f"sim{next(_labels)}"
        with _start_lock:
            traci.start(cmd, label=label)
            self.conn = traci.getConnection(label)

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:  # noqa: BLE001 — SUMO may already be gone
                pass
            self.conn = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    # --- stepping --------------------------------------------------------

    @property
    def time(self) -> float:
        return self.step_count * STEP_LENGTH

    def _inject(self) -> None:
        conn = self.conn
        if len(conn.simulation.getPendingVehicles()) > MAX_PENDING:
            return  # roads are full; queueing more just grows memory
        for vtype, origin, dest, through in self.generator.trips_for_step(self.demand, STEP_LENGTH):
            route = conn.simulation.findRoute(origin, dest, vType=vtype)
            if len(route.edges) < 2:
                self.failed_routes += 1
                continue
            vid = str(self._next_id)
            self._next_id += 1
            conn.route.add(f"r{vid}", route.edges)
            conn.vehicle.add(
                vid, f"r{vid}", typeID=vtype,
                depart="now", departLane="best", departSpeed="max",
                departPos="base" if through else "random_free",
            )
            self.vtype_of[vid] = VTYPE_INDEX[vtype]

    def step(self, want_frame: bool = True) -> dict | None:
        conn = self.conn
        self._inject()
        update_edges = (self.step_count + 1) % EDGE_UPDATE_EVERY == 0
        need_vehicles = want_frame or update_edges
        junction = self._context_junction
        if need_vehicles:
            conn.junction.subscribeContext(junction, tc.CMD_GET_VEHICLE_VARIABLE, CONTEXT_RANGE, VEHICLE_VARS)
        conn.simulationStep()
        self.step_count += 1
        now = self.time
        self.running = conn.vehicle.getIDCount()

        for vid in conn.simulation.getDepartedIDList():
            self.depart_time[vid] = now
            self.total_departed += 1
        for vid in conn.simulation.getArrivedIDList():
            self.total_arrived += 1
            dep = self.depart_time.pop(vid, None)
            delay = self.last_timeloss.pop(vid, 0.0)
            self.vtype_of.pop(vid, None)
            if dep is not None:
                self.recent_trips.append((now, now - dep, delay))
        self.total_teleports += conn.simulation.getStartingTeleportNumber()
        while self.recent_trips and self.recent_trips[0][0] < now - STATS_WINDOW:
            self.recent_trips.popleft()

        if not need_vehicles:
            return None
        # Vehicles mid-teleport report INVALID_DOUBLE_VALUE (-2^30); skip them.
        results = {
            vid: r for vid, r in (conn.junction.getContextSubscriptionResults(junction) or {}).items()
            if r[tc.VAR_SPEED] > tc.INVALID_DOUBLE_VALUE
        }
        conn.junction.unsubscribeContext(junction, tc.CMD_GET_VEHICLE_VARIABLE, CONTEXT_RANGE)
        for vid, r in results.items():
            self.last_timeloss[vid] = r[tc.VAR_TIMELOSS]

        if update_edges:
            self._update_edge_levels(results)
            self._edges_dirty = True

        if not want_frame:
            return None
        frame = self._frame(results, include_edges=self._edges_dirty)
        self._edges_dirty = False
        return frame

    def _update_edge_levels(self, results: dict) -> None:
        # How fast vehicles move relative to how fast *they* could go here, so a
        # road full of free-flowing rickshaws isn't painted as a jam.
        sums: dict[str, list[float]] = {}
        edge_speed = self.area.edge_speed
        for vid, r in results.items():
            edge = r[tc.VAR_ROAD_ID]
            limit = edge_speed.get(edge)
            if limit is None:  # inside a junction
                continue
            vmax = min(limit, VTYPE_MAX_SPEED[self.vtype_of.get(vid, 0)])
            acc = sums.setdefault(edge, [0.0, 0])
            acc[0] += min(1.0, r[tc.VAR_SPEED] / vmax)
            acc[1] += 1
        levels = self.edge_level
        for edge, (total, n) in sums.items():
            levels[edge] = 0.6 * levels.get(edge, 1.0) + 0.4 * (total / n)
        for edge in list(levels):
            if edge not in sums:
                levels[edge] = 0.8 * levels[edge] + 0.2  # recover towards free flow
                if levels[edge] > 0.97:
                    del levels[edge]

    def _stats(self, speeds: np.ndarray, timeloss: np.ndarray) -> dict:
        now = self.time
        n = len(speeds)
        trips = self.recent_trips
        window = min(now, STATS_WINDOW) or 1
        return {
            "time": now,
            "clock": self.options.start_hour * 3600 + now,
            "running": n,
            "waiting": len(self.conn.simulation.getPendingVehicles()),
            "departed": self.total_departed,
            "arrived": self.total_arrived,
            "teleports": self.total_teleports,
            "failed_routes": self.failed_routes,
            "avg_speed_kmh": round(float(speeds.mean()) * 3.6, 1) if n else 0.0,
            "stopped": int((speeds < 0.5).sum()) if n else 0,
            "avg_trip_min": round(sum(t[1] for t in trips) / len(trips) / 60, 2) if trips else None,
            "avg_delay_min": round(sum(t[2] for t in trips) / len(trips) / 60, 2) if trips else None,
            # Completed trips alone hide gridlock (stuck vehicles never finish).
            "on_road_delay_min": round(float(timeloss.mean()) / 60, 2) if n else 0.0,
            "throughput_per_hour": round(len(trips) * 3600 / window),
        }

    def _frame(self, results: dict, include_edges: bool) -> dict:
        ids = list(results)
        if ids:
            pos = np.array([results[v][tc.VAR_POSITION] for v in ids], dtype=np.float64)
            lon, lat = self.area.projection.to_lonlat(pos[:, 0], pos[:, 1])
            speeds = np.array([results[v][tc.VAR_SPEED] for v in ids])
            angles = np.array([results[v][tc.VAR_ANGLE] for v in ids])
            timeloss = np.array([results[v][tc.VAR_TIMELOSS] for v in ids])
        else:
            lon = lat = speeds = angles = timeloss = np.empty(0)
        frame = {
            "type": "frame",
            "ids": [int(v) for v in ids],
            "lon": np.round(lon, 6).tolist(),
            "lat": np.round(lat, 6).tolist(),
            "angle": np.round(angles).astype(int).tolist(),
            "speed": np.round(speeds, 1).tolist(),
            "kind": [self.vtype_of.get(v, 0) for v in ids],
            "stats": self._stats(speeds, timeloss),
        }
        if include_edges:
            frame["edges"] = {e: round(v, 2) for e, v in self.edge_level.items()}
        return frame
