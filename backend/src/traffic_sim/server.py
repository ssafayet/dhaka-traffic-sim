"""HTTP + WebSocket API.

REST: areas, road network GeoJSON, vehicle types, presets.
WebSocket /ws/sim: one SUMO process per connection. The simulation loop
runs in a thread and pushes frames onto an asyncio queue.

REST: layout variants (POST /api/areas/{id}/variants with U-turns, turn rules
and added signals) build an edited network; pass its id as `variant` to the network and
topology endpoints and to "start".

Client → server messages:
    {"type": "start", "area": id, "variant": id?, "demand": {...}, "options": {...},
     "closures": [...]?, "signals": {id: plan}?, "features": {...}?, "speed": 1, "warmup": 300}
    {"type": "demand", "volume": n, "mix": {...}, "through_share": x}   (applies live)
    {"type": "closures", "closures": [{"edge": id, "lanes": [i, ...]?, "from": s?, "to": s?}, ...]}
        (the full set; from/to = seconds since midnight for a timed closure; live)
    {"type": "features", "features": {bus_stops, stands, crossings, hot_zones, water, weather,
        breakdowns, elasticity}}   (the full set; see features.py; live)
    {"type": "signal", "id": tls, "plan": {"mode": "actuated|fixed|off", "phases": [...]} | null}  (live)
    {"type": "speed", "value": n}      0 = as fast as possible
    {"type": "pause"} / {"type": "play"} / {"type": "stop"}
Server → client:
    {"type": "status", "state": "starting|warming_up|running|paused|stopped", ...}
    {"type": "frame", ids, lon, lat, angle, speed, kind, stats, edges?, signals?}
    {"type": "closures", "closed": n, "rerouted": n, "stranded": n}   after a closure change
    {"type": "signal", "id": tls}                       after a signal change
    {"type": "features", "rerouted": n, "stranded": n}  after a features change
    {"type": "warning", "message": str}                 an edit was refused; the run goes on
    {"type": "error", "message": str}
"""

import argparse
import asyncio
import os
import queue
import subprocess
import threading
import time
from dataclasses import asdict, fields
from typing import Annotated, Literal

import traci
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import scenario
from .config import AREAS_DIR, BACKEND_DIR, DEFAULT_AREA, VARIANTS_DIR
from .demand import DemandSettings
from .engine import SimOptions, Simulation, list_areas, load_area
from .presets import DEFAULT_PRESET, PRESETS
from .features import defaults as feature_defaults
from .features import parse_features
from .scenario import EditError, parse_closures, parse_signal
from .vtypes import VEHICLE_TYPES

MAX_SIMS = int(os.environ.get("TRAFFIC_SIM_MAX_SIMS", "4"))
MAX_BUILDS = 2  # netconvert runs at once (layout edits)
MAX_WARMUP = 3600  # s
# Vehicles per hour a run may ask for, on Farmgate (which the presets are tuned
# on; twice the UI slider's top). Other areas scale it by their size, as the
# presets do (area.json demand_scale), so a whole-city rush hour isn't cut off.
MAX_VOLUME_FARMGATE = 40_000
MAX_FPS = 20  # cap on frames sent per second
WARMUP_FPS = 4
# Whole-city runs have tens of thousands of vehicles; a frame costs roughly
# 50 bytes each, so send fewer frames as the count grows (the browser
# interpolates between them).
VEHICLES_PER_SECOND = 150_000
MIN_FPS = 2

app = FastAPI(title="Traffic Sim")
_active = threading.BoundedSemaphore(MAX_SIMS)
_builds = threading.BoundedSemaphore(MAX_BUILDS)


@app.get("/api/areas")
def areas():
    return {"default": DEFAULT_AREA, "areas": list_areas()}


@app.get("/api/areas/{area_id}/network")
def network(area_id: str, variant: str | None = None):
    if variant is None:
        path = AREAS_DIR / area_id / "network.geojson"
        if not path.is_file() or path.parent.parent != AREAS_DIR:
            raise HTTPException(404, "unknown area")
    else:
        path = VARIANTS_DIR / area_id / variant / "network.geojson"
        if not scenario.VARIANT_ID.fullmatch(variant) or not path.is_file() or path.parents[2] != VARIANTS_DIR:
            raise HTTPException(404, "unknown road layout")
    return FileResponse(path, media_type="application/geo+json")


def _area_or_404(area_id: str, variant: str | None = None):
    try:
        return load_area(area_id, variant)
    except KeyError:
        raise HTTPException(404, "unknown area or road layout") from None


@app.get("/api/areas/{area_id}/features")
def features(area_id: str):
    """Default road features: OpenStreetMap bus stops and news-reported waterlogging spots."""
    area = _area_or_404(area_id)
    return feature_defaults(area, AREAS_DIR / area.id)


@app.get("/api/areas/{area_id}/topology")
def topology(area_id: str, variant: str | None = None):
    """Junctions, traffic signals (with their default programs) and street pairs."""
    return _area_or_404(area_id, variant).topology()


Id = Annotated[str, Field(min_length=1, max_length=200)]


class UTurnEdit(BaseModel):
    edge: Id
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    both_directions: bool = True


class JunctionUTurnEdit(BaseModel):
    junction: Id
    allow: bool


class JunctionTurnEdit(BaseModel):
    junction: Id
    allow: Literal["straight_left", "left"]


class LayoutEdits(BaseModel):
    uturns: list[UTurnEdit] = Field(default=[], max_length=scenario.MAX_UTURNS)
    junction_uturns: list[JunctionUTurnEdit] = Field(default=[], max_length=scenario.MAX_JUNCTION_EDITS)
    junction_turns: list[JunctionTurnEdit] = Field(default=[], max_length=scenario.MAX_JUNCTION_EDITS)
    signals: list[Id] = Field(default=[], max_length=scenario.MAX_JUNCTION_EDITS)


@app.post("/api/areas/{area_id}/variants")
def create_variant(area_id: str, edits: LayoutEdits):
    """Build the area's network with these layout edits; returns its id.

    Always relative to the unedited area. No edits → variant null (the area).
    """
    area = _area_or_404(area_id)
    data = edits.model_dump()
    if scenario.is_empty(data):
        return {"variant": None, "uturns": []}
    if not _builds.acquire(blocking=False):
        raise HTTPException(503, "Busy building other road layouts; try again in a moment.")
    try:
        variant, uturns = scenario.build_variant(area, data)
    except EditError as e:
        return JSONResponse({"detail": str(e), "errors": e.errors}, status_code=422)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        print(f"[variant {area_id}] {e}")
        raise HTTPException(500, "Could not build the road layout.") from None
    finally:
        _builds.release()
    return {"variant": variant, "uturns": uturns}


@app.get("/api/vehicle-types")
def vehicle_types():
    return [
        {
            "id": vt.id, "label": vt.label, "color": vt.color,
            "length": vt.length, "width": vt.width,
            "max_speed_kmh": round(vt.max_speed * 3.6),
            "default_share": vt.default_share,
        }
        for vt in VEHICLE_TYPES
    ]


@app.get("/api/presets")
def presets():
    return {"default": DEFAULT_PRESET, "presets": PRESETS}


def max_volume(area) -> float:
    return MAX_VOLUME_FARMGATE * max(1.0, float(area.meta.get("demand_scale", 1)))


def _demand_from(msg: dict, max_vol: float, base: DemandSettings | None = None) -> DemandSettings:
    d = base or DemandSettings()
    if "volume" in msg:
        d.volume = max(0.0, min(max_vol, float(msg["volume"])))
    if "mix" in msg:
        known = {vt.id for vt in VEHICLE_TYPES}
        d.mix = {k: float(v) for k, v in msg["mix"].items() if k in known}
    if "through_share" in msg:
        d.through_share = max(0.0, min(1.0, float(msg["through_share"])))
    return d


def _live_edits(area, msg: dict):
    """Closures, signal plans and features to start a run with. ValueError if malformed."""
    closures = parse_closures(area, msg.get("closures") or [])
    features = parse_features(area, msg.get("features"))
    raw_signals = msg.get("signals") or {}
    if not isinstance(raw_signals, dict):
        raise ValueError("signals must map signal ids to plans")
    signals = {}
    for tls, raw in raw_signals.items():
        if tls not in area.signals:
            continue  # a signal from another layout
        plan = parse_signal(area, tls, raw)
        if plan is not None:
            signals[tls] = plan
    return closures, signals, features


MAX_REROUTE_EVERY = 1800  # s


def _options_from(msg: dict) -> SimOptions:
    allowed = {f.name for f in fields(SimOptions)}
    opts = {k: v for k, v in (msg or {}).items() if k in allowed}
    if "reroute_every" in opts:
        v = opts["reroute_every"]
        opts["reroute_every"] = int(min(MAX_REROUTE_EVERY, max(0, v))) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0
    return SimOptions(**opts)


class SimRunner(threading.Thread):
    def __init__(self, sim: Simulation, emit, speed: float, warmup: float):
        super().__init__(daemon=True)
        self.sim = sim
        self.emit = emit
        self.speed = speed
        self.warmup = warmup
        self.paused = False
        self.commands: queue.Queue = queue.Queue()
        self._stopping = False

    def send(self, cmd: dict) -> None:
        self.commands.put(cmd)

    def _handle(self, cmd: dict) -> None:
        kind = cmd.get("type")
        if kind == "demand":
            self.sim.demand = _demand_from(cmd, max_volume(self.sim.area), self.sim.demand)
        elif kind == "speed":
            self.speed = max(0.0, float(cmd.get("value", 1)))
        elif kind == "pause":
            self.paused = True
            self.emit({"type": "status", "state": "paused"})
        elif kind == "play":
            self.paused = False
            self.emit({"type": "status", "state": "running"})
        elif kind == "stop":
            self._stopping = True
        elif kind == "closures":
            try:
                closures = parse_closures(self.sim.area, cmd.get("closures"))
            except ValueError as e:
                self.emit({"type": "warning", "message": f"Road closures not applied: {e}"})
                return
            rerouted, stranded = self.sim.set_closures(closures)
            self.emit({"type": "closures", "closed": len(closures), "rerouted": rerouted, "stranded": stranded})
        elif kind == "signal":
            tls = cmd.get("id")
            try:
                self.sim.set_signal(tls, parse_signal(self.sim.area, tls, cmd.get("plan")))
            except (ValueError, traci.TraCIException) as e:
                self.emit({"type": "warning", "message": f"Signal settings not applied: {e}"})
                return
            self.emit({"type": "signal", "id": tls})
        elif kind == "features":
            try:
                f = parse_features(self.sim.area, cmd.get("features"))
            except ValueError as e:
                self.emit({"type": "warning", "message": f"Road features not applied: {e}"})
                return
            rerouted, stranded = self.sim.set_features(f)
            for w in f.warnings[:3]:
                self.emit({"type": "warning", "message": w})
            self.emit({"type": "features", "rerouted": rerouted, "stranded": stranded})

    def run(self) -> None:
        try:
            self.sim.start()
            last_sent = 0.0
            while not self._stopping:
                try:
                    while True:
                        self._handle(self.commands.get_nowait())
                except queue.Empty:
                    pass
                if self._stopping:
                    break
                if self.paused:
                    try:
                        self._handle(self.commands.get(timeout=0.2))
                    except queue.Empty:
                        pass
                    continue

                warming = self.sim.time < self.warmup
                fast = warming or self.speed == 0
                t0 = time.monotonic()
                fps = WARMUP_FPS if warming else MAX_FPS
                fps = max(MIN_FPS, min(fps, VEHICLES_PER_SECOND / max(1, self.sim.running)))
                want = t0 - last_sent >= 1 / fps - 0.005
                frame = self.sim.step(want_frame=want)
                if frame is not None:
                    frame["warming_up"] = warming
                    if warming:
                        frame["warmup_progress"] = round(self.sim.time / self.warmup, 3)
                    self.emit(frame)
                    last_sent = t0
                if not fast:
                    # One step = STEP_LENGTH simulated seconds; pace to the speed.
                    remaining = 1.0 / self.speed - (time.monotonic() - t0)
                    if remaining > 0:
                        time.sleep(remaining)
        except Exception as e:  # noqa: BLE001 — report to the client, don't die silently
            self.emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            self.sim.close()
            self.emit({"type": "status", "state": "stopped"})


@app.websocket("/ws/sim")
async def ws_sim(ws: WebSocket):
    await ws.accept()
    loop = asyncio.get_running_loop()
    out: asyncio.Queue = asyncio.Queue(maxsize=4)
    runner: SimRunner | None = None
    holding_slot = False

    def offer(msg: dict) -> None:
        # Drop the oldest queued frame rather than fall behind a slow client.
        if out.full() and msg.get("type") == "frame":
            try:
                out.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            out.put_nowait(msg)
        except asyncio.QueueFull:
            pass

    def emit(msg: dict) -> None:
        try:
            loop.call_soon_threadsafe(offer, msg)
        except RuntimeError:
            pass  # the connection closed while the simulation was finishing

    async def pump():
        while True:
            await ws.send_json(await out.get())

    pump_task = asyncio.create_task(pump())

    def stop_runner():
        nonlocal runner, holding_slot
        if runner is not None:
            runner.send({"type": "stop"})
            runner.join(timeout=10)
            runner = None
        if holding_slot:
            _active.release()
            holding_slot = False

    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "start":
                await asyncio.to_thread(stop_runner)
                try:
                    area = await asyncio.to_thread(load_area, msg.get("area") or DEFAULT_AREA, msg.get("variant"))
                except KeyError:
                    offer({"type": "error", "message": "unknown area or road layout"})
                    continue
                try:
                    closures, signals, features = await asyncio.to_thread(_live_edits, area, msg)
                except ValueError as e:
                    offer({"type": "error", "message": str(e)})
                    continue
                if not _active.acquire(blocking=False):
                    offer({"type": "error", "message": "server busy: too many simulations running"})
                    continue
                holding_slot = True
                sim = await asyncio.to_thread(
                    Simulation, area, _demand_from(msg.get("demand") or {}, max_volume(area)), _options_from(msg.get("options")),
                    closures, signals, features,
                )
                offer({"type": "status", "state": "starting", "options": asdict(sim.options)})
                runner = SimRunner(
                    sim, emit,
                    speed=max(0.0, float(msg.get("speed", 1))),
                    warmup=max(0.0, min(MAX_WARMUP, float(msg.get("warmup", 300)))),
                )
                runner.start()
            elif kind == "stop":
                await asyncio.to_thread(stop_runner)
            elif runner is not None:
                runner.send(msg)
    except WebSocketDisconnect:
        pass
    finally:
        pump_task.cancel()
        await asyncio.to_thread(stop_runner)


# Serve the built frontend in production (`npm run build` in ../frontend).
_dist = BACKEND_DIR.parent / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")


def main() -> None:
    import uvicorn

    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    args = p.parse_args()
    uvicorn.run("traffic_sim.server:app", host=args.host, port=args.port, reload=args.reload)
