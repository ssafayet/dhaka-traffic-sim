"""HTTP + WebSocket API.

REST: areas, road network GeoJSON, vehicle types, presets.
WebSocket /ws/sim: one SUMO process per connection. The simulation loop
runs in a thread and pushes frames onto an asyncio queue.

Client → server messages:
    {"type": "start", "area": id, "demand": {...}, "options": {...}, "speed": 1, "warmup": 300}
    {"type": "demand", "volume": n, "mix": {...}, "through_share": x}   (applies live)
    {"type": "speed", "value": n}      0 = as fast as possible
    {"type": "pause"} / {"type": "play"} / {"type": "stop"}
Server → client:
    {"type": "status", "state": "starting|warming_up|running|paused|stopped", ...}
    {"type": "frame", ids, lon, lat, angle, speed, kind, stats, edges?}
    {"type": "error", "message": str}
"""

import argparse
import asyncio
import os
import queue
import threading
import time
from dataclasses import asdict, fields

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import AREAS_DIR, BACKEND_DIR, DEFAULT_AREA
from .demand import DemandSettings
from .engine import SimOptions, Simulation, list_areas, load_area
from .presets import DEFAULT_PRESET, PRESETS
from .vtypes import VEHICLE_TYPES

MAX_SIMS = int(os.environ.get("TRAFFIC_SIM_MAX_SIMS", "4"))
MAX_FPS = 20  # cap on frames sent per second
WARMUP_FPS = 4
# Whole-city runs have tens of thousands of vehicles; a frame costs roughly
# 50 bytes each, so send fewer frames as the count grows (the browser
# interpolates between them).
VEHICLES_PER_SECOND = 150_000
MIN_FPS = 2

app = FastAPI(title="Traffic Sim")
_active = threading.BoundedSemaphore(MAX_SIMS)


@app.get("/api/areas")
def areas():
    return {"default": DEFAULT_AREA, "areas": list_areas()}


@app.get("/api/areas/{area_id}/network")
def network(area_id: str):
    path = AREAS_DIR / area_id / "network.geojson"
    if not path.is_file() or path.parent.parent != AREAS_DIR:
        raise HTTPException(404, "unknown area")
    return FileResponse(path, media_type="application/geo+json")


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


def _demand_from(msg: dict, base: DemandSettings | None = None) -> DemandSettings:
    d = base or DemandSettings()
    if "volume" in msg:
        d.volume = max(0.0, min(40000.0, float(msg["volume"])))
    if "mix" in msg:
        known = {vt.id for vt in VEHICLE_TYPES}
        d.mix = {k: float(v) for k, v in msg["mix"].items() if k in known}
    if "through_share" in msg:
        d.through_share = max(0.0, min(1.0, float(msg["through_share"])))
    return d


def _options_from(msg: dict) -> SimOptions:
    allowed = {f.name for f in fields(SimOptions)}
    return SimOptions(**{k: v for k, v in (msg or {}).items() if k in allowed})


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
            self.sim.demand = _demand_from(cmd, self.sim.demand)
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
        loop.call_soon_threadsafe(offer, msg)

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
                    area = await asyncio.to_thread(load_area, msg.get("area") or DEFAULT_AREA)
                except KeyError:
                    offer({"type": "error", "message": "unknown area"})
                    continue
                if not _active.acquire(blocking=False):
                    offer({"type": "error", "message": "server busy: too many simulations running"})
                    continue
                holding_slot = True
                sim = await asyncio.to_thread(
                    Simulation, area, _demand_from(msg.get("demand") or {}), _options_from(msg.get("options"))
                )
                offer({"type": "status", "state": "starting", "options": asdict(sim.options)})
                runner = SimRunner(
                    sim, emit,
                    speed=float(msg.get("speed", 1)),
                    warmup=max(0.0, float(msg.get("warmup", 300))),
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
