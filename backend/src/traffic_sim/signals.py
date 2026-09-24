"""Where Dhaka's traffic signals actually run automatically.

Most junctions OpenStreetMap marks with a signal are, in practice, directed by
traffic police: the lights are dark or ignored. Automatic signals run only on a
few stretches (2025–26):

* the Shahbag – Bangla Motor – Karwan Bazar – Farmgate – Bijoy Sarani – PMO –
  Jahangir Gate – Mohakhali railgate corridor,
* Gulshan 1 and Gulshan 2 circles,
* the junctions inside Dhaka Cantonment.

`is_automated` decides, by position, whether a signal runs automatically by
default; every other signal starts switched off (see Simulation.set_signal),
which is how the simulator stands in for police control. Users can switch any
signal on or off, and a signal they add runs automatically.

`add_automated_signals` runs when an area is prepared: it puts a signal on the
junctions of these stretches that OpenStreetMap doesn't mark as signalled.

The zones are approximate; adjust them here.
"""

import math
import subprocess
import tempfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

import sumolib

# (name, lon, lat, radius in m): junctions that have an automatic signal. The
# radius covers every node of the crossing (divided roads, roundabouts).
AUTOMATED_JUNCTIONS: list[tuple[str, float, float, float]] = [
    ("shahbag", 90.39590, 23.73812, 45),
    ("intercontinental", 90.39600, 23.74136, 45),
    ("bangla_motor", 90.39483, 23.74588, 40),
    ("karwan_bazar", 90.39320, 23.74990, 50),  # SAARC fountain (Sonargaon) circle: a roundabout
    ("farmgate", 90.39000, 23.75860, 60),
    ("bijoy_sarani", 90.38902, 23.76441, 45),
    ("pmo", 90.38921, 23.76845, 40),  # Agargaon link road, by the Prime Minister's Office
    ("old_airport", 90.38970, 23.77097, 35),
    ("jahangir_gate", 90.38998, 23.77531, 50),
    ("mohakhali_railgate", 90.39818, 23.77814, 40),
    ("gulshan_1", 90.41679, 23.78042, 60),
    ("gulshan_2", 90.41425, 23.79483, 60),
]
# The corridor itself (lon, lat): signals within CORRIDOR_BUFFER of it are automatic.
CORRIDOR: list[tuple[float, float]] = [
    (90.3959, 23.7381),  # Shahbag
    (90.3960, 23.7414),
    (90.3948, 23.7459),  # Bangla Motor
    (90.3932, 23.7499),  # Karwan Bazar
    (90.3900, 23.7586),  # Farmgate
    (90.3890, 23.7644),  # Bijoy Sarani
    (90.3892, 23.7685),  # PMO
    (90.3900, 23.7753),  # Jahangir Gate
    (90.3940, 23.7770),
    (90.3982, 23.7781),  # Mohakhali railgate
]
CORRIDOR_BUFFER = 70.0  # m
# Dhaka Cantonment, west of Airport Road (lon, lat).
CANTONMENT: list[tuple[float, float]] = [
    (90.3880, 23.7775),
    (90.3950, 23.7790),
    (90.3975, 23.7850),
    (90.3990, 23.7905),
    (90.4005, 23.7990),
    (90.4018, 23.8070),
    (90.4030, 23.8145),
    (90.4080, 23.8190),
    (90.4110, 23.8230),
    (90.4100, 23.8350),
    (90.3880, 23.8350),
]
MAJOR_ROADS = {f"highway.{t}" for t in ("motorway", "trunk", "primary", "secondary", "tertiary")}
MAJOR_LINKS = {f"{t}_link" for t in MAJOR_ROADS}
# A new Cantonment signal keeps this far from an existing one.
SIGNAL_SPACING = 80.0  # m
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


def in_cantonment(lon: float, lat: float) -> bool:
    return _inside((lon, lat), CANTONMENT)


def is_automated(lon: float, lat: float) -> bool:
    """Whether a signal here runs automatically by default (else police-directed)."""
    p = (lon, lat)
    if any(_distance(p, (x, y)) <= r + 30 for _, x, y, r in AUTOMATED_JUNCTIONS):
        return True
    if any(_to_segment(p, a, b) <= CORRIDOR_BUFFER for a, b in zip(CORRIDOR, CORRIDOR[1:])):
        return True
    return in_cantonment(lon, lat)


def _roads_in(node) -> list:
    return [e for e in node.getIncoming() if not e.getFunction()]


def _is_crossing(node) -> bool:
    if node.getType() in ("internal", "dead_end"):
        return False
    near = {e.getFromNode().getID() for e in _roads_in(node)} | {e.getToNode().getID() for e in node.getOutgoing() if not e.getFunction()}
    return len(near) >= 3


def _plan(net) -> dict[str, list]:
    """New traffic light id → the nodes it controls."""
    lonlat = {}
    for n in net.getNodes():
        x, y = n.getCoord()
        lonlat[n.getID()] = net.convertXY2LonLat(x, y)
    signalled = [lonlat[n.getID()] for n in net.getNodes() if n.getType() == "traffic_light"]
    on_roundabout = {nid for r in net.getRoundabouts() for nid in r.getNodes()}
    out: dict[str, list] = {}

    for name, lon, lat, radius in AUTOMATED_JUNCTIONS:
        nodes = [
            n for n in net.getNodes()
            if _distance(lonlat[n.getID()], (lon, lat)) <= radius and _is_crossing(n)
            # Crossings of main roads, not side streets joining them.
            and sum(e.getType() in MAJOR_ROADS or e.getType() in MAJOR_LINKS for e in _roads_in(n)) >= 2
        ]
        # A roundabout keeps its give-way rules: signals on a circle's short
        # segments back traffic up round it (tried on the SAARC circle).
        if nodes and not any(n.getType() == "traffic_light" or n.getID() in on_roundabout for n in nodes):
            out[PREFIX + name] = nodes

    taken = {n.getID() for nodes in out.values() for n in nodes}
    for n in net.getNodes():
        at = lonlat[n.getID()]
        if n.getID() in taken or n.getID() in on_roundabout or n.getType() == "traffic_light" or not in_cantonment(*at):
            continue
        roads = _roads_in(n)
        if len(roads) < 3 or sum(e.getType() in MAJOR_ROADS for e in roads) < 2 or not _is_crossing(n):
            continue
        if any(_distance(at, s) < SIGNAL_SPACING for s in signalled):
            continue
        out[f"{PREFIX}cantonment_{n.getID()}"] = [n]
        signalled.append(at)
    return out


def add_automated_signals(net_file: Path) -> int:
    """Signal the automatic junctions OSM leaves unsignalled; rewrites the network. Returns how many."""
    net = sumolib.net.readNet(str(net_file), withInternal=False)
    plan = _plan(net)
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
