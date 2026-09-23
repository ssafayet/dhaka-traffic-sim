"""One running SUMO simulation, driven step by step over TraCI."""

import itertools
import json
import random
import re
import shutil
import tempfile
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import sumolib
import traci
import traci.constants as tc

from . import scenario
from .config import AREAS_DIR, VARIANTS_DIR
from .demand import DemandGenerator, DemandSettings, EdgePools
from .geo import NetProjection
from .features import CROWD_SLOWDOWN, KERB_STOPPERS, RAIN_EFFECT, VENDOR_SPEED_ONE_LANE, WATER_DEPTH, Features
from .scenario import ClosureRule, SignalPlan, closures_now
from .vtypes import CROWD_TYPE, PARKED_PREFIX, VEHICLE_TYPES, vclass_for, vtypes_xml

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
TIMED_CHECK_EVERY = 10  # steps between checks of timed closures
PARKED_FOREVER = 10**7  # s
CROWD_GRACE = 20  # s a crossing crowd may wait for a gap in traffic before giving up

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
    reroute_every: int = 0  # s between drivers re-planning on current travel times; 0 = never


class Area:
    """A prepared area on disk (see prepare.py), or a variant of one with the
    user's layout edits (see scenario.py). Loaded once and shared."""

    def __init__(self, area_dir: Path):
        self.dir = area_dir
        self.meta = json.loads((area_dir / "area.json").read_text())
        self.variant: str | None = self.meta.get("variant")
        self.net_file = area_dir / "net.net.xml"
        self.net = sumolib.net.readNet(str(self.net_file), withInternal=False, withPrograms=True)
        self.projection = NetProjection.from_net_file(self.net_file)
        self.edge_speed = {e.getID(): e.getSpeed() for e in self.net.getEdges()}
        roads = [e for e in self.net.getEdges() if not e.getFunction()]
        self.edge_lanes = {e.getID(): e.getLaneNumber() for e in roads}
        self.edge_length = {e.getID(): e.getLength() for e in roads}
        # As built, to restore after closures, flooding and zones.
        self.lane_allowed = {ln.getID(): frozenset(ln.getPermissions()) for e in roads for ln in e.getLanes()}
        self.lane_speed = {ln.getID(): ln.getSpeed() for e in roads for ln in e.getLanes()}
        self.twins = scenario.twins(self.net)
        self._topology: dict | None = None
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

    def topology(self) -> dict:
        """Junctions and signals for the road editor (cached)."""
        with self._pools_lock:
            if self._topology is None:
                self._topology = scenario.topology(self)
                self._signals = {s["id"]: s for s in self._topology["signals"]}
            return self._topology

    @property
    def crossings(self) -> dict[str, tuple[str, ...]]:
        """Junction → every junction of its crossing (see scenario.crossings)."""
        return {j["id"]: tuple(j["group"]) for j in self.topology()["junctions"]}

    @property
    def signals(self) -> dict[str, dict]:
        """Traffic signal id → its default program and links (see scenario.topology)."""
        self.topology()
        return self._signals


_area_cache: dict[str, Area] = {}
_variant_cache: OrderedDict[tuple[str, str], Area] = OrderedDict()
KEEP_VARIANTS_LOADED = 4  # a whole-city network is a few hundred MB in memory
_area_lock = threading.Lock()  # the city network takes seconds to load; load it once


def list_areas() -> list[dict]:
    if not AREAS_DIR.exists():
        return []
    return [
        json.loads((d / "area.json").read_text())
        for d in sorted(AREAS_DIR.iterdir())
        if (d / "area.json").exists() and (d / "net.net.xml").exists()
    ]


def load_area(area_id: str, variant: str | None = None) -> Area:
    """The prepared area, or its variant with layout edits. KeyError if unknown."""
    if not isinstance(area_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", area_id):
        raise KeyError(area_id)
    if variant is not None and not (isinstance(variant, str) and scenario.VARIANT_ID.fullmatch(variant)):
        raise KeyError(variant)
    with _area_lock:
        if variant is None:
            if area_id not in _area_cache:
                area_dir = AREAS_DIR / area_id
                if not (area_dir / "area.json").exists():
                    raise KeyError(area_id)
                _area_cache[area_id] = Area(area_dir)
            return _area_cache[area_id]
        key = (area_id, variant)
        if key not in _variant_cache:
            area_dir = VARIANTS_DIR / area_id / variant
            if not (area_dir / "area.json").exists():
                raise KeyError(variant)
            _variant_cache[key] = Area(area_dir)
            while len(_variant_cache) > KEEP_VARIANTS_LOADED:
                _variant_cache.popitem(last=False)
        _variant_cache.move_to_end(key)
        return _variant_cache[key]


class Simulation:
    def __init__(
        self,
        area: Area,
        demand: DemandSettings,
        options: SimOptions,
        closures: "dict[str, frozenset[int] | None] | list[ClosureRule] | None" = None,
        signals: dict[str, SignalPlan] | None = None,
        features: Features | None = None,
    ):
        self.area = area
        self.demand = demand
        self.options = options
        # Applied once SUMO has started.
        self._initial_closures = _rules(closures)
        self._initial_signals = signals or {}
        self._initial_features = features
        self.generator = DemandGenerator(
            {vt.id: area.pools(vclass_for(vt, options.rickshaws_on_main_roads)) for vt in VEHICLE_TYPES},
            seed=options.seed,
        )
        self.rng = random.Random(options.seed)
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
        self._vclass = {vt.id: vclass_for(vt, options.rickshaws_on_main_roads) for vt in VEHICLE_TYPES}
        # Live edits. Routes are kept so a closure only reroutes the vehicles
        # heading for it.
        self.routes: dict[str, tuple[str, ...]] = {}
        self.closure_rules: list[ClosureRule] = []
        self.closures: dict[str, frozenset[int] | None] = {}  # in force now
        self.closed_edges: set[str] = set()  # every lane closed
        self.blocked: dict[str, set[str]] = {}  # vehicle class → edges it can't use (closures, water)
        self._lanes: dict[str, tuple[bool, frozenset[str], float | None]] = {}  # lane → applied (closed, banned, cap)
        self.signal_plans: dict[str, SignalPlan] = {}
        self.signal_ids = sorted(area.signals)
        self._programs = itertools.count()
        # Road features (features.py).
        self.features = Features()
        self._bus_stops: dict[str, list] = {}  # edge → bus stops on it
        self._pickups: dict[str, dict[str, list]] = {}  # vehicle type → edge → stands
        self._kerb_zones: list = []  # hot zones where kerbside stops happen
        self.special: set[str] = set()  # parked vehicles and crowds: not traffic
        self._crowds: set[str] = set()
        self._crowds_of: dict[str, tuple[list[str], float]] = {}  # crossing → (its crowds, when to clear them)
        self._parked: dict[str, list[str]] = {}  # stand id → its parked vehicles
        self._stand_key: dict[str, tuple] = {}
        self._crossing_due: dict[str, float] = {}  # crossing id → next time people cross
        self.crossing_until: dict[str, float] = {}  # crossing id → end of the current crossing
        self.broken: dict[str, float] = {}  # broken-down vehicle → when it moves again
        self.total_breakdowns = 0
        self._rain_applied = "none"
        self._base_tau: dict[str, float] = {}
        self.demand_factor = 1.0
        self._delay_min = 0.0

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
        if o.reroute_every > 0:
            # Drivers re-plan every so often on the travel times they see.
            cmd += [
                "--device.rerouting.probability", "1",
                "--device.rerouting.period", str(o.reroute_every),
                "--device.rerouting.adaptation-interval", "10",
            ]
        label = f"sim{next(_labels)}"
        with _start_lock:
            traci.start(cmd, label=label)
            self.conn = traci.getConnection(label)
        if self._initial_features is not None:
            self.set_features(self._initial_features, refresh=False)
        if self._initial_closures:
            self.closure_rules = list(self._initial_closures)
        self._refresh_lanes()
        for tls, plan in self._initial_signals.items():
            self.set_signal(tls, plan)

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

    @property
    def clock(self) -> float:
        return self.options.start_hour * 3600 + self.time

    def _new_id(self) -> str:
        vid = str(self._next_id)
        self._next_id += 1
        return vid

    def _inject(self) -> None:
        conn = self.conn
        if len(conn.simulation.getPendingVehicles()) > MAX_PENDING:
            return  # roads are full; queueing more just grows memory
        demand = self.demand
        if self.features.elasticity:
            # Travellers who see bad delays stay home, go later or go another way.
            self.demand_factor = max(0.2, 1 - self.features.elasticity * self._delay_min / 10)
            demand = replace(demand, volume=demand.volume * self.demand_factor)
        else:
            self.demand_factor = 1.0
        for vtype, origin, dest, through in self.generator.trips_for_step(demand, STEP_LENGTH):
            blocked = self.blocked.get(self._vclass[vtype], ())
            if origin in blocked or dest in blocked:
                continue  # nobody sets off from, or heads for, a road closed to them
            # Routing sees closures: closed lanes are closed to every class.
            route = conn.simulation.findRoute(origin, dest, vType=vtype)
            if len(route.edges) < 2:
                self.failed_routes += 1
                continue
            vid = self._new_id()
            try:
                conn.route.add(f"r{vid}", route.edges)
                conn.vehicle.add(
                    vid, f"r{vid}", typeID=vtype,
                    depart="now", departLane="best", departSpeed="max",
                    departPos="base" if through else "random_free",
                )
            except traci.TraCIException:
                self.failed_routes += 1
                continue
            self.vtype_of[vid] = VTYPE_INDEX[vtype]
            self.routes[vid] = route.edges
            self._add_stops(vid, vtype, route.edges)

    def _kerb_lane(self, edge: str, vclass: str) -> int | None:
        """The lane nearest the kerb that this class may use now."""
        for i in range(self.area.edge_lanes[edge]):
            closed, banned, _ = self._lanes.get(f"{edge}_{i}", (False, frozenset(), None))
            if not closed and vclass not in banned:
                return i
        return None

    def _stop(self, vid: str, vclass: str, edge: str, pos: float, duration: float) -> None:
        lane = self._kerb_lane(edge, vclass)
        if lane is None:
            return
        length = self.area.edge_length[edge]
        try:
            self.conn.vehicle.setStop(vid, edge, pos=min(max(1.0, pos), length - 0.5), laneIndex=lane, duration=duration)
        except traci.TraCIException:
            pass  # e.g. it sets off past this point; it just doesn't stop

    def _add_stops(self, vid: str, vtype: str, route) -> None:
        """Bus stops, fare pickups and kerbside stops along a new vehicle's route."""
        rng, vclass = self.rng, self._vclass[vtype]
        if vtype == "bus" and self._bus_stops:
            for e in route:
                for s in self._bus_stops.get(e, ()):
                    self._stop(vid, vclass, e, s.at.pos, s.dwell * rng.uniform(0.6, 1.4))
        stands = self._pickups.get(vtype)
        if stands:
            for e in route:
                for s in stands.get(e, ()):
                    if rng.random() < s.pickup:
                        # Just past the parked ones.
                        self._stop(vid, vclass, e, s.at.pos + 4, s.dwell * rng.uniform(0.5, 1.5))
        if vtype in KERB_STOPPERS:
            for zone in self._kerb_zones:
                if rng.random() >= zone.kerb_stops:
                    continue
                inside = [e for e in route[1:] if e in zone.edges]
                if inside:
                    e = rng.choice(inside)
                    length = self.area.edge_length[e]
                    self._stop(vid, vclass, e, rng.uniform(0.2, 0.8) * length, rng.uniform(8, 30))

    # --- live edits ------------------------------------------------------

    def set_closures(self, closures) -> tuple[int, int]:
        """Close exactly these roads (None) or lanes; reopen everything else.

        Accepts edge → lanes (always) or ClosureRule list (possibly timed).
        Returns (rerouted, stranded): vehicles sent around newly closed roads,
        and those with no other way to their destination, which queue at the
        closure. Vehicles already on a closed road drive off it; nobody new
        enters.
        """
        self.closure_rules = list(_rules(closures))
        return self._refresh_lanes()

    def _lane_plan(self) -> dict[str, tuple[bool, frozenset[str], float | None]]:
        """Every lane's state from closures, flooding and hot zones: (closed, banned classes, speed cap)."""
        lanes = self.area.edge_lanes
        plan: dict[str, list] = {}

        def lane(lid):
            return plan.setdefault(lid, [False, set(), None])

        def cap(lid, v):
            st = lane(lid)
            st[2] = v if st[2] is None else min(st[2], v)

        for edge, closed in self.closures.items():
            for i in range(lanes[edge]) if closed is None else closed:
                lane(f"{edge}_{i}")[0] = True
        f = self.features
        if f.flooded:
            for zone in f.water:
                limit, banned = WATER_DEPTH[zone.depth]
                for edge in zone.edges:
                    for i in range(lanes.get(edge, 0)):
                        cap(f"{edge}_{i}", limit)
                        lane(f"{edge}_{i}")[1].update(banned)
        for zone in f.hot_zones:
            for edge in zone.edges:
                n = lanes.get(edge, 0)
                if zone.vendors and n:
                    if n > 1:
                        lane(f"{edge}_0")[0] = True
                    else:
                        cap(f"{edge}_0", VENDOR_SPEED_ONE_LANE)
                if zone.crowds:
                    for i in range(n):
                        cap(f"{edge}_{i}", self.area.lane_speed[f"{edge}_{i}"] * CROWD_SLOWDOWN)
        return {lid: (c, frozenset(b), sp) for lid, (c, b, sp) in plan.items()}

    def _refresh_lanes(self) -> tuple[int, int]:
        """Apply the lane plan for now; reroute vehicles heading into new restrictions."""
        conn = self.conn
        self.closures = closures_now(self.closure_rules, self.clock)
        want = self._lane_plan()
        tighter: set[str] = set()
        free = (False, frozenset(), None)
        for lid in want.keys() | self._lanes.keys():
            new, old = want.get(lid, free), self._lanes.get(lid, free)
            if new == old:
                continue
            closed, banned, limit = new
            if closed:
                conn.lane.setDisallowed(lid, ["all"])
            else:
                # An explicit list: an empty one would mean "everyone".
                conn.lane.setAllowed(lid, sorted(self.area.lane_allowed[lid] - banned))
            if limit != old[2]:
                conn.lane.setMaxSpeed(lid, self.area.lane_speed[lid] if limit is None else limit)
            if (closed and not old[0]) or not banned <= old[1]:
                tighter.add(lid.rsplit("_", 1)[0])
            if new == free:
                del self._lanes[lid]
            else:
                self._lanes[lid] = new
        lanes = self.area.edge_lanes
        by_edge: dict[str, list] = {}
        for lid, st in self._lanes.items():
            by_edge.setdefault(lid.rsplit("_", 1)[0], []).append(st)
        self.closed_edges = {e for e, sts in by_edge.items() if len(sts) == lanes[e] and all(c for c, _, _ in sts)}
        self.blocked = {}
        for vclass in set(self._vclass.values()):
            self.blocked[vclass] = {
                e for e, sts in by_edge.items()
                if len(sts) == lanes[e] and all(c or vclass in b for c, b, _ in sts)
            }
        return self._reroute_around(tighter) if tighter else (0, 0)

    def _reroute_around(self, edges: set[str]) -> tuple[int, int]:
        conn = self.conn
        rerouted = stranded = 0
        for vid, route in list(self.routes.items()):
            if edges.isdisjoint(route):
                continue
            try:
                index = conn.vehicle.getRouteIndex(vid)
                if index >= 0 and edges.isdisjoint(route[index + 1:]):
                    continue  # already past it (or on it, and driving off)
                conn.vehicle.rerouteTraveltime(vid)
                route = self.routes[vid] = conn.vehicle.getRoute(vid)
                vclass = self._vclass[VEHICLE_TYPES[self.vtype_of.get(vid, 0)].id]
                if self.blocked.get(vclass, set()).isdisjoint(route[max(index, 0) + 1:]):
                    rerouted += 1  # a partly closed road may still be the best way
                else:
                    stranded += 1  # no way round; SUMO keeps the old route
            except traci.TraCIException:
                # Not yet on the road and can't start there any more.
                self.routes.pop(vid, None)
                try:
                    conn.vehicle.remove(vid)
                except traci.TraCIException:
                    pass
        return rerouted, stranded

    def set_features(self, f: Features, refresh: bool = True) -> tuple[int, int]:
        """Bus stops, stands, crossings, zones and weather (see features.py)."""
        old, self.features = self.features, f
        self._bus_stops = {}
        for s in f.bus_stops:
            self._bus_stops.setdefault(s.at.edge, []).append(s)
        self._pickups = {}
        for s in f.stands:
            if s.pickup > 0:
                self._pickups.setdefault(s.kind, {}).setdefault(s.at.edge, []).append(s)
        self._kerb_zones = [z for z in f.hot_zones if z.kerb_stops > 0]
        if f.hot_zones != old.hot_zones:
            weights: dict[str, float] = {}
            for z in f.hot_zones:
                for e in z.edges:
                    weights[e] = max(weights.get(e, 1.0), z.trips)
            self.generator.set_edge_weights(weights)
        self._set_parked(f.stands)
        ids = {c.id for c in f.crossings}
        for cid in list(self._crossing_due):
            if cid not in ids:
                del self._crossing_due[cid]
                self.crossing_until.pop(cid, None)
                self._clear_crowds(cid)
        for c in f.crossings:
            self._crossing_due.setdefault(c.id, self.time + self.rng.uniform(0, c.every))
        self._set_rain(f.rain)
        return self._refresh_lanes() if refresh else (0, 0)

    def _set_parked(self, stands) -> None:
        conn = self.conn
        want = {s.id: (s.at, s.kind, s.parked) for s in stands}
        for sid in list(self._parked):
            if self._stand_key.get(sid) != want.get(sid):
                for vid in self._parked.pop(sid):
                    self.special.discard(vid)
                    self.vtype_of.pop(vid, None)
                    try:
                        conn.vehicle.remove(vid)
                    except traci.TraCIException:
                        pass
                self._stand_key.pop(sid, None)
        for s in stands:
            if s.id in self._parked or s.parked == 0:
                continue
            vt = VEHICLE_TYPES[VTYPE_INDEX[s.kind]]
            edge, length = s.at.edge, self.area.edge_length[s.at.edge]
            vids = []
            for k in range(s.parked):
                pos = s.at.pos - k * (vt.length + 0.5)
                if pos < 1:
                    break
                vid = self._new_id()
                try:
                    conn.route.add(f"r{vid}", [edge])
                    conn.vehicle.add(vid, f"r{vid}", typeID=PARKED_PREFIX + s.kind, depart="now",
                                     departPos=str(pos), departLane="0", departSpeed="0")
                    conn.vehicle.setStop(vid, edge, pos=min(length - 0.1, pos + vt.length), laneIndex=0, duration=PARKED_FOREVER)
                except traci.TraCIException:
                    continue
                vids.append(vid)
                self.special.add(vid)
                self.vtype_of[vid] = VTYPE_INDEX[s.kind]  # drawn as the rickshaw or CNG it is
            self._parked[s.id] = vids
            self._stand_key[s.id] = want[s.id]

    def _set_rain(self, rain: str) -> None:
        if rain == self._rain_applied:
            return
        speed, headway = RAIN_EFFECT[rain]
        vt = self.conn.vehicletype
        for t in VEHICLE_TYPES:
            if t.id not in self._base_tau:
                self._base_tau[t.id] = vt.getTau(t.id)
            vt.setSpeedFactor(t.id, speed)
            vt.setTau(t.id, self._base_tau[t.id] * headway)
        self._rain_applied = rain

    def _clear_crowds(self, cid: str) -> None:
        vids, _ = self._crowds_of.pop(cid, ([], 0))
        for vid in vids:
            if vid in self._crowds:
                self._crowds.discard(vid)
                self.special.discard(vid)
                try:
                    self.conn.vehicle.remove(vid)
                except traci.TraCIException:
                    pass

    def _crossings_step(self) -> None:
        """People cross: every lane at the spot (both directions) is held up for a while."""
        now = self.time
        # Crowds that couldn't get onto a busy road in time, or are still there.
        for cid in [k for k, (_, t) in self._crowds_of.items() if t <= now]:
            self._clear_crowds(cid)
        for c in self.features.crossings:
            due = self._crossing_due.get(c.id)
            if due is None or now < due or c.id in self._crowds_of:
                continue  # one group at a time
            self._crossing_due[c.id] = now + c.every * self.rng.uniform(0.6, 1.4)
            self.crossing_until[c.id] = now + c.duration
            group: list[str] = []
            self._crowds_of[c.id] = (group, now + c.duration + CROWD_GRACE)
            spots = [(c.at.edge, c.at.pos)] + ([(c.at.twin, c.at.twin_pos)] if c.at.twin else [])
            for edge, pos in spots:
                length = self.area.edge_length[edge]
                pos = min(max(1.0, pos), length - 3)
                for i in range(self.area.edge_lanes[edge]):
                    vid = self._new_id()
                    try:
                        self.conn.route.add(f"r{vid}", [edge])
                        # They leave the road as soon as they've crossed.
                        self.conn.vehicle.add(vid, f"r{vid}", typeID=CROWD_TYPE, depart="now", departPos=str(pos),
                                              departLane=str(i), departSpeed="0", arrivalPos=str(min(length, pos + 2.5)))
                        self.conn.vehicle.setStop(vid, edge, pos=min(length - 0.1, pos + 2), laneIndex=i, duration=c.duration)
                    except traci.TraCIException:
                        continue
                    self.special.add(vid)
                    self._crowds.add(vid)
                    group.append(vid)
        for cid in [k for k, t in self.crossing_until.items() if t <= now]:
            del self.crossing_until[cid]

    def _breakdowns_step(self, results: dict | None) -> None:
        now = self.time
        for vid in [v for v, t in self.broken.items() if t <= now]:
            del self.broken[vid]
        rate = self.features.breakdowns * self.area.meta.get("road_km", 100) / 100 / 3600
        if not rate or not results or self.rng.random() >= rate * STEP_LENGTH:
            return
        moving = [v for v, r in results.items() if v not in self.special and v not in self.broken and r[tc.VAR_SPEED] > 2
                  and not r[tc.VAR_ROAD_ID].startswith(":")]
        if not moving:
            return
        vid = self.rng.choice(moving)
        conn = self.conn
        try:
            edge = conn.vehicle.getRoadID(vid)
            pos = conn.vehicle.getLanePosition(vid) + 30  # room to brake
            if pos >= self.area.edge_length.get(edge, 0) - 1:
                return
            duration = self.rng.uniform(600, 1800)
            conn.vehicle.setStop(vid, edge, pos=pos, laneIndex=conn.vehicle.getLaneIndex(vid), duration=duration)
        except traci.TraCIException:
            return
        self.broken[vid] = now + duration + 60
        self.total_breakdowns += 1

    def set_signal(self, tls: str, plan: SignalPlan | None) -> None:
        """Run a user's timings on a signal (None: the network's own program)."""
        conn = self.conn
        signal = self.area.signals[tls]
        if plan is None:
            conn.trafficlight.setProgram(tls, signal["program_id"])
            self.signal_plans.pop(tls, None)
            return
        if plan.mode == "off":
            # Signal dark: drivers fall back to the junction's right of way.
            conn.trafficlight.setProgram(tls, "off")
        else:
            Phase = traci.trafficlight.Phase
            phases = [Phase(d, state, lo, hi) for state, d, lo, hi in plan.phases]
            kind = tc.TRAFFICLIGHT_TYPE_ACTUATED if plan.mode == "actuated" else tc.TRAFFICLIGHT_TYPE_STATIC
            # A fresh program id each time; SUMO keeps the old one around.
            logic = traci.trafficlight.Logic(f"user{next(self._programs)}", kind, 0, phases)
            conn.trafficlight.setProgramLogic(tls, logic)
        self.signal_plans[tls] = plan

    def step(self, want_frame: bool = True) -> dict | None:
        conn = self.conn
        self._inject()
        self._crossings_step()
        if self.step_count % TIMED_CHECK_EVERY == 0 and any(r.start is not None for r in self.closure_rules):
            if closures_now(self.closure_rules, self.clock) != self.closures:
                self._refresh_lanes()
        update_edges = (self.step_count + 1) % EDGE_UPDATE_EVERY == 0
        need_vehicles = want_frame or update_edges or self.features.breakdowns > 0
        junction = self._context_junction
        if need_vehicles:
            conn.junction.subscribeContext(junction, tc.CMD_GET_VEHICLE_VARIABLE, CONTEXT_RANGE, VEHICLE_VARS)
        conn.simulationStep()
        self.step_count += 1
        now = self.time
        special = self.special

        for vid in conn.simulation.getDepartedIDList():
            if vid in special:
                continue
            self.depart_time[vid] = now
            self.total_departed += 1
        for vid in conn.simulation.getArrivedIDList():
            if vid in special:
                special.discard(vid)
                self._crowds.discard(vid)
                continue
            self.total_arrived += 1
            dep = self.depart_time.pop(vid, None)
            delay = self.last_timeloss.pop(vid, 0.0)
            self.vtype_of.pop(vid, None)
            self.routes.pop(vid, None)
            self.broken.pop(vid, None)
            if dep is not None:
                self.recent_trips.append((now, now - dep, delay))
        self.total_teleports += conn.simulation.getStartingTeleportNumber()
        while self.recent_trips and self.recent_trips[0][0] < now - STATS_WINDOW:
            self.recent_trips.popleft()
        self.running = conn.vehicle.getIDCount() - len(special)

        if not need_vehicles:
            return None
        # Vehicles mid-teleport report INVALID_DOUBLE_VALUE (-2^30); skip them.
        # Crowds aren't drawn; parked vehicles are, but aren't traffic.
        results = {
            vid: r for vid, r in (conn.junction.getContextSubscriptionResults(junction) or {}).items()
            if r[tc.VAR_SPEED] > tc.INVALID_DOUBLE_VALUE and vid not in self._crowds
        }
        conn.junction.unsubscribeContext(junction, tc.CMD_GET_VEHICLE_VARIABLE, CONTEXT_RANGE)
        traffic = {vid: r for vid, r in results.items() if vid not in special}
        for vid, r in traffic.items():
            self.last_timeloss[vid] = r[tc.VAR_TIMELOSS]
        self._breakdowns_step(traffic)

        if update_edges:
            self._update_edge_levels(traffic)
            self._edges_dirty = True

        if not want_frame:
            return None
        frame = self._frame(results, traffic, include_edges=self._edges_dirty)
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
        delay = round(float(timeloss.mean()) / 60, 2) if n else 0.0
        self._delay_min = delay
        return {
            "time": now,
            "clock": self.clock,
            "running": n,
            "waiting": max(0, len(self.conn.simulation.getPendingVehicles()) - len(self.special)),
            "departed": self.total_departed,
            "arrived": self.total_arrived,
            "teleports": self.total_teleports,
            "failed_routes": self.failed_routes,
            "avg_speed_kmh": round(float(speeds.mean()) * 3.6, 1) if n else 0.0,
            "stopped": int((speeds < 0.5).sum()) if n else 0,
            "avg_trip_min": round(sum(t[1] for t in trips) / len(trips) / 60, 2) if trips else None,
            "avg_delay_min": round(sum(t[2] for t in trips) / len(trips) / 60, 2) if trips else None,
            # Completed trips alone hide gridlock (stuck vehicles never finish).
            "on_road_delay_min": delay,
            "throughput_per_hour": round(len(trips) * 3600 / window),
            "demand_factor": round(self.demand_factor, 2),
            "broken_down": len(self.broken),
        }

    def _frame(self, results: dict, traffic: dict, include_edges: bool) -> dict:
        ids = list(results)
        if ids:
            pos = np.array([results[v][tc.VAR_POSITION] for v in ids], dtype=np.float64)
            lon, lat = self.area.projection.to_lonlat(pos[:, 0], pos[:, 1])
            angles = np.array([results[v][tc.VAR_ANGLE] for v in ids])
            speeds_all = np.array([results[v][tc.VAR_SPEED] for v in ids])
        else:
            lon = lat = angles = speeds_all = np.empty(0)
        speeds = np.array([r[tc.VAR_SPEED] for r in traffic.values()])
        timeloss = np.array([r[tc.VAR_TIMELOSS] for r in traffic.values()])
        frame = {
            "type": "frame",
            "ids": [int(v) for v in ids],
            "lon": np.round(lon, 6).tolist(),
            "lat": np.round(lat, 6).tolist(),
            "angle": np.round(angles).astype(int).tolist(),
            "speed": np.round(speeds_all, 1).tolist(),
            "kind": [self.vtype_of.get(v, 0) for v in ids],
            "stats": self._stats(speeds, timeloss),
        }
        if include_edges:
            frame["edges"] = {e: round(v, 2) for e, v in self.edge_level.items()}
        if self.signal_ids:
            tl = self.conn.trafficlight
            frame["signals"] = {t: tl.getPhase(t) for t in self.signal_ids}
        if self.crossing_until:
            frame["crossing"] = sorted(self.crossing_until)
        if self.broken:
            index = {v: i for i, v in enumerate(ids)}
            frame["broken"] = [[frame["lon"][index[v]], frame["lat"][index[v]]] for v in self.broken if v in index]
        return frame


def _rules(closures) -> list[ClosureRule]:
    if not closures:
        return []
    if isinstance(closures, dict):
        return [ClosureRule(e, lanes) for e, lanes in closures.items()]
    return list(closures)
