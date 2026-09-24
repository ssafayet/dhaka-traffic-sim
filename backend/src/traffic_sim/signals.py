"""Which traffic signals run automatically, by a region's signal policy.

A region's signals.toml (see region.SignalPolicy) lists the junctions,
corridors and zones where signals run automatically, and says what happens
everywhere else: "on" (every mapped signal runs, as in most cities) or "off"
(signals start switched off, which is how the simulator stands in for traffic
police directing junctions, as in Dhaka).

`is_automated` decides, by position, whether a signal runs automatically by
default; the rest start switched off (see Simulation.set_signal). Users can
switch any signal on or off, and a signal they add runs automatically.

`add_automated_signals` runs when an area is prepared: it puts a signal on the
listed junctions, and on the main-road crossings of zones with add_missing,
that OpenStreetMap doesn't mark as signalled.
"""

import math
import subprocess
import tempfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

import sumolib

from .region import SignalPolicy

MAJOR_ROADS = {f"highway.{t}" for t in ("motorway", "trunk", "primary", "secondary", "tertiary")}
MAJOR_LINKS = {f"{t}_link" for t in MAJOR_ROADS}
# A signal added in a zone keeps this far from an existing one.
SIGNAL_SPACING = 80.0  # m
# A signal this far beyond a listed junction's radius still counts as that junction's.
JUNCTION_SLACK = 30.0  # m
PREFIX = "auto_"  # traffic light ids of the signals added here

M_PER_DEG_LAT = 111_320.0


def _metres(lon: float, lat: float, lon0: float, lat0: float) -> tuple[float, float]:
    return (lon - lon0) * M_PER_DEG_LAT * math.cos(math.radians(lat0)), (lat - lat0) * M_PER_DEG_LAT


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(*_metres(a[0], a[1], b[0], b[1]))


def _to_segment(p, a, b) -> float:
    ax, ay = _metres(a[0], a[1], p[0], p[1])
    bx, by = _metres(b[0], b[1], p[0], p[1])
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy))) if dx or dy else 0.0
    return math.hypot(ax + t * dx, ay + t * dy)


def _inside(p, polygon) -> bool:
    x, y = p
    inside = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def is_automated(policy: SignalPolicy, lon: float, lat: float) -> bool:
    """Whether a signal here runs automatically by default (else switched off)."""
    if policy.elsewhere == "on":
        return True
    p = (lon, lat)
    if any(_distance(p, (j.lon, j.lat)) <= j.radius + JUNCTION_SLACK for j in policy.junctions):
        return True
    for c in policy.corridors:
        if any(_to_segment(p, a, b) <= c.buffer for a, b in zip(c.points, c.points[1:])):
            return True
    return any(_inside(p, list(z.polygon)) for z in policy.zones)


def _roads_in(node) -> list:
    return [e for e in node.getIncoming() if not e.getFunction()]


def _is_crossing(node) -> bool:
    if node.getType() in ("internal", "dead_end"):
        return False
    near = {e.getFromNode().getID() for e in _roads_in(node)} | {e.getToNode().getID() for e in node.getOutgoing() if not e.getFunction()}
    return len(near) >= 3


def _plan(net, policy: SignalPolicy) -> dict[str, list]:
    """New traffic light id → the nodes it controls."""
    lonlat = {}
    for n in net.getNodes():
        x, y = n.getCoord()
        lonlat[n.getID()] = net.convertXY2LonLat(x, y)
    signalled = [lonlat[n.getID()] for n in net.getNodes() if n.getType() == "traffic_light"]
    on_roundabout = {nid for r in net.getRoundabouts() for nid in r.getNodes()}
    out: dict[str, list] = {}

    for j in policy.junctions:
        nodes = [
            n for n in net.getNodes()
            if _distance(lonlat[n.getID()], (j.lon, j.lat)) <= j.radius and _is_crossing(n)
            # Crossings of main roads, not side streets joining them.
            and sum(e.getType() in MAJOR_ROADS or e.getType() in MAJOR_LINKS for e in _roads_in(n)) >= 2
        ]
        # A roundabout keeps its give-way rules: signals on a circle's short
        # segments back traffic up round it (tried on the SAARC circle).
        if nodes and not any(n.getType() == "traffic_light" or n.getID() in on_roundabout for n in nodes):
            out[PREFIX + j.name] = nodes

    taken = {n.getID() for nodes in out.values() for n in nodes}
    for zone in policy.zones:
        if not zone.add_missing:
            continue
        polygon = list(zone.polygon)
        for n in net.getNodes():
            at = lonlat[n.getID()]
            if n.getID() in taken or n.getID() in on_roundabout or n.getType() == "traffic_light" or not _inside(at, polygon):
                continue
            roads = _roads_in(n)
            if len(roads) < 3 or sum(e.getType() in MAJOR_ROADS for e in roads) < 2 or not _is_crossing(n):
                continue
            if any(_distance(at, s) < SIGNAL_SPACING for s in signalled):
                continue
            out[f"{PREFIX}{zone.name}_{n.getID()}"] = [n]
            taken.add(n.getID())
            signalled.append(at)
    return out


def add_automated_signals(net_file: Path, policy: SignalPolicy) -> int:
    """Signal the automatic junctions OSM leaves unsignalled; rewrites the network. Returns how many."""
    net = sumolib.net.readNet(str(net_file), withInternal=False)
    plan = _plan(net, policy)
    if not plan:
        return 0
    lines = []
    for tl, nodes in plan.items():
        for n in nodes:
            x, y = n.getCoord()
            lines.append(
                f'  <node id={quoteattr(n.getID())} x="{x:.2f}" y="{y:.2f}" type="traffic_light" tl={quoteattr(tl)}/>'
            )
    with tempfile.TemporaryDirectory() as tmp:
        nodes_file = Path(tmp) / "signals.nod.xml"
        nodes_file.write_text("<nodes>\n" + "\n".join(lines) + "\n</nodes>\n")
        out = Path(tmp) / "net.net.xml"
        cmd = [
            "netconvert", "--sumo-net-file", str(net_file), "--node-files", str(nodes_file),
            "--tls.default-type", "actuated", "-o", str(out), "--no-warnings",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError("netconvert failed adding signals: " + (result.stderr or result.stdout).strip()[-300:])
        out.replace(net_file)
    return len(plan)
