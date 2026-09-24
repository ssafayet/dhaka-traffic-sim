"""User changes to an area's roads: what can be edited, and building edited networks.

Two kinds of change:

* Live changes (road and lane closures, signal timings) are applied over TraCI
  to a running simulation; see Simulation.set_closures / set_signal. This module
  only validates them.
* Layout changes (U-turns, turn rules, added signals) change the network's structure,
  which SUMO cannot do at run time. They are applied to the area's prepared
  network with netconvert, producing a *variant* network that a simulation is
  then started on. Variants are always built from the unmodified area, so the
  edits describe the whole layout, not a change to the previous variant.

Mid-block U-turns: main roads are often divided carriageways, as Dhaka's are
(two one-way OSM ways), and a U-turn is a gap in the median. It is built by splitting both
carriageways at the chosen point and joining the split nodes with a short
connector road. On an undivided two-way street both directions are split at one
shared node, where vehicles turn around.

Turn rules ("straight and left only", "left only") keep only those movements
at a junction. A crossing of divided roads is mapped as several junctions a
few metres apart, where a U-turn can be two left turns in a row, so the
crossing is first merged into one junction (netconvert <join>) and the banned
movements are then removed from it.
"""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import quoteattr

import sumolib
from sumolib.geomhelper import polygonOffsetWithMinimumDistanceToPoint, positionAtShapeOffset

from .config import VARIANTS_DIR
from .prepare import build_geojson
from .signals import PREFIX as AUTO_SIGNAL, is_automated

VARIANT_ID = re.compile(r"[0-9a-f]{16}")
# Part of every variant id: bump it when a change here builds different
# networks from the same edits, so stale builds aren't reused.
BUILD_FORMAT = 4
MAX_CLOSURES = 2000
MAX_UTURNS = 50
MAX_JUNCTION_EDITS = 100
MAX_SIGNAL_PHASE_S = 300
UTURN_MIN_GAP = 15.0  # m a U-turn keeps from each end of its road and from other U-turns
OPPOSITE_SEARCH_RADIUS = 60.0  # m to look for the other carriageway
UTURN_SPEED = 5.56  # m/s (20 km/h) through the median gap
KEEP_VARIANTS = 30  # built networks kept per area
BUILD_TIMEOUT = 600  # s; the whole-city network takes a while
TURNAROUND = ("t", "T")  # sumolib link directions for a U-turn
SIGNAL_MODES = ("actuated", "fixed", "off")
# Junctions this close are one crossing (the carriageways of a divided road):
# linked by a road this short, or this near each other on the same streets
# (a closed median leaves the halves unconnected). A flyover above a junction
# has its own name, so it stays separate. The whole crossing stays compact.
CROSSING_LINK_MAX = 20.0  # m
CROSSING_NEAR_MAX = 25.0  # m
CROSSING_SPAN_MAX = 60.0  # m
# SUMO link direction → movement. Left is the kerb-side turn: Bangladesh
# drives on the left, so right turns and U-turns cut across oncoming traffic.
MOVEMENT = {"s": "straight", "l": "left", "L": "left", "r": "right", "R": "right", "t": "uturn", "T": "uturn"}
TURN_RULES = {
    "straight_left": ("Straight and left only", {"straight", "left"}),
    "left": ("Left only", {"left"}),
}
UTURN_NODE = re.compile(r"ut\d+[ab]?")  # split nodes made for mid-block U-turns

_build_locks: dict[str, threading.Lock] = {}
_build_locks_guard = threading.Lock()


class EditError(ValueError):
    """Edits that can't be applied; `errors` says which and why, for the UI."""

    def __init__(self, errors: list[dict]):
        super().__init__("; ".join(e["message"] for e in errors))
        self.errors = errors


# --- geometry ------------------------------------------------------------------


def _bearing(a, b) -> float:
    """Compass bearing in degrees from point a to b (net XY: x east, y north)."""
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360


def _angle_diff(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def _heading_at(shape, offset: float) -> float:
    """Driving direction at `offset` metres along a polyline."""
    walked = 0.0
    for a, b in zip(shape, shape[1:]):
        seg = math.dist(a, b)
        if walked + seg >= offset or b is shape[-1]:
            return _bearing(a, b)
        walked += seg
    return _bearing(shape[0], shape[-1])


def _shape_length(shape) -> float:
    return sum(math.dist(a, b) for a, b in zip(shape, shape[1:]))


def _offset_on(edge, point) -> float:
    """Where `point` projects onto the edge, in the edge's own length units."""
    shape = edge.getShape()
    offset = polygonOffsetWithMinimumDistanceToPoint(point, shape)
    return offset * edge.getLength() / (_shape_length(shape) or 1)


def _point_on(edge, pos: float):
    shape = edge.getShape()
    return positionAtShapeOffset(shape, pos * _shape_length(shape) / (edge.getLength() or 1))


COMPASS = ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")


def _compass(bearing: float) -> str:
    return COMPASS[round(bearing / 45) % 8]


# --- topology: what the editor shows and edits refer to -------------------------


def _is_road(edge) -> bool:
    return not edge.getFunction()


def twins(net: sumolib.net.Net) -> dict[str, str]:
    """Edge → the other direction of the same undivided street."""
    out = {}
    for e in net.getEdges():
        if not _is_road(e):
            continue
        back = [o for o in e.getToNode().getOutgoing() if _is_road(o) and o.getToNode() == e.getFromNode()]
        if back:
            out[e.getID()] = min(back, key=lambda o: abs(o.getLength() - e.getLength())).getID()
    return out


def _neighbours(node) -> set[str]:
    ids = {e.getFromNode().getID() for e in node.getIncoming() if _is_road(e)}
    return ids | {e.getToNode().getID() for e in node.getOutgoing() if _is_road(e)}


def _is_intersection(node) -> bool:
    return node.getType() not in ("internal", "dead_end") and len(_neighbours(node)) >= 3


def crossings(net, joined: dict[str, list[str]] | None = None) -> dict[str, tuple[str, ...]]:
    """Intersection node → every node of the crossing it belongs to.

    `joined`: nodes of a built variant that were merged from several nodes of
    the unedited area; they keep those ids, which is what edits refer to.
    """
    nodes = [n for n in net.getNodes() if _is_intersection(n) and not UTURN_NODE.fullmatch(n.getID())]
    ids = {n.getID() for n in nodes}
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def names(n):
        return {e.getName() for e in (*n.getIncoming(), *n.getOutgoing()) if _is_road(e) and e.getName()}

    grid = defaultdict(list)
    for n in nodes:
        x, y = n.getCoord()
        grid[(int(x // CROSSING_NEAR_MAX), int(y // CROSSING_NEAR_MAX))].append(n)
    for n in nodes:
        for e in n.getOutgoing():
            to = e.getToNode().getID()
            if _is_road(e) and to in ids and e.getLength() <= CROSSING_LINK_MAX:
                parent[find(n.getID())] = find(to)
        x, y = n.getCoord()
        cx, cy = int(x // CROSSING_NEAR_MAX), int(y // CROSSING_NEAR_MAX)
        mine = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for o in grid[(cx + dx, cy + dy)]:
                    if o.getID() <= n.getID() or math.dist(o.getCoord(), (x, y)) > CROSSING_NEAR_MAX:
                        continue
                    mine = names(n) if mine is None else mine
                    if mine & names(o):
                        parent[find(n.getID())] = find(o.getID())
    members = defaultdict(list)
    for i in ids:
        members[find(i)].append(i)
    out: dict[str, tuple[str, ...]] = {}
    for group in members.values():
        group.sort()
        coords = [net.getNode(m).getCoord() for m in group]
        compact = len(group) == 1 or max(math.dist(a, b) for a in coords for b in coords) <= CROSSING_SPAN_MAX
        for m in group:
            out[m] = tuple(group) if compact else (m,)
    for node_id, base_ids in (joined or {}).items():
        if node_id in out:
            out[node_id] = tuple(sorted(base_ids))
    return out


def _link_direction(in_lane, out_lane) -> str:
    for c in in_lane.getOutgoing():
        if c.getToLane() == out_lane:
            return c.getDirection()
    return "s"


def topology(area) -> dict:
    """Junctions, signals and street pairs of an area, for the editor.

    Signal links are listed by link index; each phase's state string has one
    character per link (G/g green, y yellow, r red).
    """
    net = area.net
    to_lonlat = area.projection.to_lonlat

    def lonlat(points):
        lon, lat = to_lonlat([p[0] for p in points], [p[1] for p in points])
        return [(round(float(a), 6), round(float(b), 6)) for a, b in zip(lon, lat)]

    signals = []
    node_signal: dict[str, str] = {}
    # Junctions (as in the unedited area) the user put a signal on.
    added = set(area.meta.get("added_signals", ()))
    joined = area.meta.get("joined") or {}
    for tls in net.getTrafficLights():
        programs = tls.getPrograms()
        if not programs:
            continue
        program_id, program = next(iter(programs.items()))
        links: dict[int, dict] = {}
        nodes = {}
        for in_lane, out_lane, index in tls.getConnections():
            src, dst = in_lane.getEdge(), out_lane.getEdge()
            node = src.getToNode()
            nodes[node.getID()] = node.getCoord()
            node_signal[node.getID()] = tls.getID()
            shape = src.getShape()
            links[index] = {
                "from": src.getID(),
                "name": src.getName() or "",
                # Where the approach comes from, opposite to its driving direction.
                "approach": _compass((_bearing(shape[-2], shape[-1]) + 180) % 360),
                "to_name": dst.getName() or "",
                "dir": _link_direction(in_lane, out_lane),
            }
        if not links:
            continue
        cx = sum(c[0] for c in nodes.values()) / len(nodes)
        cy = sum(c[1] for c in nodes.values()) / len(nodes)
        (lon, lat), = lonlat([(cx, cy)])
        originals = {o for n in nodes for o in joined.get(n, (n,))}
        signals.append({
            "id": tls.getID(),
            "lon": lon,
            "lat": lat,
            "program_id": program_id,
            "mode": "actuated" if program.getType() == "actuated" else "fixed",
            # Runs automatically unless switched off; the rest start off (the region's signals.toml).
            "automated": tls.getID().startswith(AUTO_SIGNAL) or bool(originals & added) or is_automated(area.region.signals, lon, lat),
            "phases": [
                {
                    "state": ph.state,
                    "duration": ph.duration,
                    "min": ph.minDur if ph.minDur >= 0 else ph.duration,
                    "max": ph.maxDur if ph.maxDur >= 0 else ph.duration,
                }
                for ph in program.getPhases()
            ],
            "links": [links.get(i) for i in range(max(links) + 1)],
        })

    junctions = [n for n in net.getNodes() if _is_intersection(n)]
    coords = lonlat([n.getCoord() for n in junctions]) if junctions else []
    groups = crossings(net, area.meta.get("joined"))
    junction_list = []
    for n, (lon, lat) in zip(junctions, coords):
        roads_in = [e for e in n.getIncoming() if _is_road(e)]
        uturns = sum(
            1
            for e in roads_in
            for conns in e.getOutgoing().values()
            if any(c.getDirection() in TURNAROUND for c in conns)
        )
        names = sorted({e.getName() for e in roads_in if e.getName()})
        junction_list.append({
            "id": n.getID(),
            "lon": lon,
            "lat": lat,
            "signal": node_signal.get(n.getID()),
            "approaches": len(roads_in),
            "uturns": uturns,
            "names": names[:4],
            # The whole crossing; turn rules apply to all of it. Ids of the
            # unedited area, so edits made on a variant still refer to them.
            "group": list(groups.get(n.getID(), (n.getID(),))),
        })
    return {"junctions": junction_list, "signals": signals, "twins": twins(net)}


# --- live edits ----------------------------------------------------------------


DAY = 86400


@dataclass(frozen=True)
class ClosureRule:
    """Closed road (lanes None) or lanes, always or between two times of day."""

    edge: str
    lanes: frozenset[int] | None
    start: float | None = None  # s since midnight; None = always
    end: float | None = None

    def active(self, clock: float) -> bool:
        if self.start is None:
            return True
        t, s, e = clock % DAY, self.start % DAY, self.end % DAY
        return s <= t < e if s < e else (t >= s or t < e)  # windows may cross midnight


def closures_now(rules, clock: float) -> dict[str, frozenset[int] | None]:
    """Edge → closed lanes (None = all) for the rules in force at `clock`."""
    out: dict[str, frozenset[int] | None] = {}
    for r in rules:
        if not r.active(clock):
            continue
        if r.lanes is None or out.get(r.edge, frozenset()) is None:
            out[r.edge] = None
        else:
            out[r.edge] = out.get(r.edge, frozenset()) | r.lanes
    return out


def parse_closures(area, raw) -> list[ClosureRule]:
    """[{edge, lanes?, from?, to?}] (from/to: seconds since midnight) → rules."""
    if not isinstance(raw, list) or len(raw) > MAX_CLOSURES:
        raise ValueError(f"closures must be a list of at most {MAX_CLOSURES}")
    out: list[ClosureRule] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each closure is {edge, lanes?, from?, to?}")
        edge = item.get("edge")
        n = area.edge_lanes.get(edge) if isinstance(edge, str) else None
        if n is None:
            continue  # a road from another layout; nothing to close here
        start, end = item.get("from"), item.get("to")
        if (start is None) != (end is None):
            raise ValueError("a timed closure needs both from and to")
        if start is not None and not all(isinstance(v, (int, float)) and 0 <= v <= DAY for v in (start, end)):
            raise ValueError("closure times are seconds since midnight")
        lanes = item.get("lanes")
        if lanes is not None:
            if not isinstance(lanes, list) or not all(isinstance(i, int) and 0 <= i < n for i in lanes):
                raise ValueError(f"lanes of {edge} must be indexes below {n}")
            if not lanes:
                continue
            lanes = None if len(set(lanes)) == n else frozenset(lanes)
        out.append(ClosureRule(edge, lanes, start, end))
    return out


@dataclass(frozen=True)
class SignalPlan:
    mode: str  # actuated | fixed | off
    phases: tuple[tuple[str, float, float, float], ...] = ()  # (state, duration, min, max)


def parse_signal(area, tls_id, raw) -> SignalPlan | None:
    """A user's signal settings, checked against the signal's own phases.

    Only timings and the control mode can change; which movements are green in
    each phase comes from the network. None = back to the network's program.
    """
    signal = area.signals.get(tls_id) if isinstance(tls_id, str) else None
    if signal is None:
        raise ValueError(f"no traffic signal {tls_id!r} in this area")
    if raw is None:
        return None
    if not isinstance(raw, dict) or raw.get("mode") not in SIGNAL_MODES:
        raise ValueError(f"signal mode must be one of {', '.join(SIGNAL_MODES)}")
    if raw["mode"] == "off":
        return SignalPlan("off")
    phases = raw.get("phases")
    base = signal["phases"]
    if not isinstance(phases, list) or len(phases) != len(base):
        raise ValueError(f"signal {tls_id} has {len(base)} phases")

    def seconds(v, what: str) -> float:
        if not isinstance(v, (int, float)) or not 1 <= v <= MAX_SIGNAL_PHASE_S:
            raise ValueError(f"{what} must be 1–{MAX_SIGNAL_PHASE_S} s")
        return float(v)

    out = []
    for i, (p, b) in enumerate(zip(phases, base), 1):
        if not isinstance(p, dict):
            raise ValueError("each phase is {duration, min?, max?}")
        duration = seconds(p.get("duration"), f"phase {i} duration")
        lo = seconds(p.get("min", duration), f"phase {i} minimum")
        hi = seconds(p.get("max", duration), f"phase {i} maximum")
        if raw["mode"] == "fixed":
            lo = hi = duration
        elif not lo <= duration <= hi:
            raise ValueError(f"phase {i}: minimum ≤ duration ≤ maximum")
        out.append((b["state"], duration, lo, hi))
    return SignalPlan(raw["mode"], tuple(out))


# --- layout edits → variant network ----------------------------------------------


@dataclass
class _Split:
    pos: float
    node: str
    tag: str  # suffix of the road piece after the split
    edit: int  # index of the U-turn edit, for errors


@dataclass
class _Plan:
    splits: dict[str, list[_Split]] = field(default_factory=dict)
    new_edges: list[str] = field(default_factory=list)
    # (from edge, from split tag or None, to edge, to split tag or None, from lane, to lane)
    uturn_links: list[tuple] = field(default_factory=list)
    connections: list[str] = field(default_factory=list)
    deletions: list[str] = field(default_factory=list)
    tls: list[str] = field(default_factory=list)
    # (crossing node ids, a road entering it, rule, edit index)
    turn_rules: list[tuple[tuple[str, ...], str, str, int]] = field(default_factory=list)
    connectors: set[str] = field(default_factory=set)
    resolved: list[dict] = field(default_factory=list)


def _canonical(edits: dict) -> dict:
    return {
        "uturns": [
            {"edge": u["edge"], "lon": round(u["lon"], 6), "lat": round(u["lat"], 6),
             "both_directions": bool(u.get("both_directions", True))}
            for u in edits.get("uturns", [])
        ],
        "junction_uturns": sorted(
            ({"junction": j["junction"], "allow": bool(j["allow"])} for j in edits.get("junction_uturns", [])),
            key=lambda j: j["junction"],
        ),
        "junction_turns": sorted(
            ({"junction": j["junction"], "allow": j["allow"]} for j in edits.get("junction_turns", [])),
            key=lambda j: j["junction"],
        ),
        "signals": sorted(set(edits.get("signals", []))),
    }


def is_empty(edits: dict) -> bool:
    return not any(edits.get(k) for k in ("uturns", "junction_uturns", "junction_turns", "signals"))


def variant_id(edits: dict) -> str:
    blob = json.dumps([BUILD_FORMAT, _canonical(edits)], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _find_opposite(net, edge, point, pos, twin_id):
    """The road carrying the other direction at this point, and where on it."""
    if twin_id:
        twin = net.getEdge(twin_id)
        return twin, _offset_on(twin, point), "undivided"
    heading = _heading_at(edge.getShape(), pos)
    best = None
    for other, dist in net.getNeighboringEdges(point[0], point[1], OPPOSITE_SEARCH_RADIUS):
        if other is edge or not _is_road(other) or not other.allows("passenger"):
            continue
        at = _offset_on(other, point)
        if _angle_diff(_heading_at(other.getShape(), at), heading) < 150:
            continue
        # Prefer the same street: flyovers and parallel roads can be closer.
        score = dist + (0 if other.getName() == edge.getName() else 30) + (0 if other.getType() == edge.getType() else 15)
        if best is None or score < best[0]:
            best = (score, other, at)
    if best is None:
        return None
    return best[1], best[2], "divided"


def _plan(area, edits: dict) -> _Plan:
    net = area.net
    twin_of = area.twins
    plan = _Plan()
    errors = []

    def fail(kind, index, message):
        errors.append({"kind": kind, "index": index, "message": message})

    for i, u in enumerate(edits.get("uturns", [])):
        eid = u["edge"]
        if eid not in area.edge_lanes:
            fail("uturn", i, "unknown road")
            continue
        edge = net.getEdge(eid)
        x, y = net.convertLonLat2XY(u["lon"], u["lat"])
        pos = _offset_on(edge, (x, y))
        name = edge.getName() or "this road"
        if not UTURN_MIN_GAP <= pos <= edge.getLength() - UTURN_MIN_GAP:
            fail("uturn", i, f"U-turn on {name} is too close to a junction; move it along the road")
            continue
        point = _point_on(edge, pos)
        found = _find_opposite(net, edge, point, pos, twin_of.get(eid))
        if found is None:
            fail("uturn", i, f"No road in the other direction next to {name}")
            continue
        other, other_pos, kind = found
        if not UTURN_MIN_GAP <= other_pos <= other.getLength() - UTURN_MIN_GAP:
            fail("uturn", i, f"The other side of {name} has a junction here; move the U-turn along the road")
            continue
        tag = f"ut{i}"
        both = bool(u.get("both_directions", True))
        if kind == "undivided":
            # Both directions split at one node, where vehicles turn around.
            plan.splits.setdefault(eid, []).append(_Split(pos, tag, tag, i))
            plan.splits.setdefault(other.getID(), []).append(_Split(other_pos, tag, tag, i))
            plan.uturn_links.append((eid, tag, other.getID(), tag, edge.getLaneNumber() - 1, other.getLaneNumber() - 1))
            if both:
                plan.uturn_links.append((other.getID(), tag, eid, tag, other.getLaneNumber() - 1, edge.getLaneNumber() - 1))
        else:
            a, b = f"{tag}a", f"{tag}b"
            plan.splits.setdefault(eid, []).append(_Split(pos, a, tag, i))
            plan.splits.setdefault(other.getID(), []).append(_Split(other_pos, b, tag, i))
            allow = " ".join(sorted(edge.getLane(0).getPermissions()))
            gaps = [(f"{tag}.gap", a, b, edge)]
            if both:
                gaps.append((f"{tag}.gapr", b, a, other))
            for cid, src, dst, s_edge in gaps:
                plan.new_edges.append(
                    f'<edge id={quoteattr(cid)} from={quoteattr(src)} to={quoteattr(dst)} numLanes="1" '
                    f'speed="{UTURN_SPEED}" allow={quoteattr(allow)}/>'
                )
                plan.connectors.add(cid)
                # Into the median gap from the lane next to the median (the
                # highest index: Bangladesh drives on the left).
                plan.uturn_links.append((s_edge.getID(), tag, cid, None, s_edge.getLaneNumber() - 1, 0))
        plan.resolved.append({
            "index": i, "kind": kind, "edge": eid, "name": edge.getName() or "",
            "opposite": other.getID(), "opposite_name": other.getName() or "",
        })

    # Splits on one road are chained: each piece is named after the U-turn
    # that starts it, so connections can refer to the pieces either side.
    for eid, splits in plan.splits.items():
        splits.sort(key=lambda s: s.pos)
        for prev, cur in zip(splits, splits[1:]):
            if cur.pos - prev.pos < UTURN_MIN_GAP and cur.node != prev.node:
                fail("uturn", cur.edit, "Too close to another U-turn")

    def piece_before(eid, tag):
        prev = eid
        for s in plan.splits[eid]:
            if s.tag == tag:
                return prev
            prev = f"{eid}.{s.tag}"
        raise KeyError(tag)

    for src, s_tag, dst, d_tag, from_lane, to_lane in plan.uturn_links:
        from_edge = piece_before(src, s_tag)
        to_edge = f"{dst}.{d_tag}" if d_tag else dst
        plan.connections.append(
            f'<connection from={quoteattr(from_edge)} to={quoteattr(to_edge)} '
            f'fromLane="{from_lane}" toLane="{to_lane}"/>'
        )

    groups = area.crossings
    ruled: dict[tuple[str, ...], int] = {}
    for i, t in enumerate(edits.get("junction_turns", [])):
        group = groups.get(t["junction"])
        if group is None:
            fail("junction_turn", i, "unknown junction")
            continue
        if t["allow"] not in TURN_RULES:
            fail("junction_turn", i, f"turn rule must be one of {', '.join(TURN_RULES)}")
            continue
        if group in ruled:
            fail("junction_turn", i, "This junction already has a turn rule")
            continue
        ruled[group] = i
        entry = next(
            (e for m in group for e in net.getNode(m).getIncoming() if _is_road(e) and e.getFromNode().getID() not in group),
            None,
        )
        if entry is None:
            fail("junction_turn", i, "No road leads into this junction")
            continue
        eid = entry.getID()
        if eid in plan.splits:  # a U-turn splits it; the last piece leads in
            eid = f"{eid}.{plan.splits[eid][-1].tag}"
        plan.turn_rules.append((group, eid, t["allow"], i))

    for i, j in enumerate(edits.get("junction_uturns", [])):
        jid = j["junction"]
        if not net.hasNode(jid) or not _is_intersection(net.getNode(jid)):
            fail("junction_uturn", i, "unknown junction")
            continue
        if groups.get(jid) in ruled:
            fail("junction_uturn", i, "The junction's turn rule already bans U-turns")
            continue
        node = net.getNode(jid)
        for src in node.getIncoming():
            if not _is_road(src):
                continue
            outgoing = src.getOutgoing()
            if j["allow"]:
                if any(c.getDirection() in TURNAROUND for cs in outgoing.values() for c in cs):
                    continue
                heading = _heading_at(src.getShape(), src.getLength())
                back = [
                    o for o in node.getOutgoing()
                    if _is_road(o) and o not in outgoing and o.allows("passenger")
                    and _angle_diff(_heading_at(o.getShape(), 0), heading) >= 135
                ]
                if back:
                    dst = max(back, key=lambda o: _angle_diff(_heading_at(o.getShape(), 0), heading))
                    plan.connections.append(
                        f'<connection from={quoteattr(src.getID())} to={quoteattr(dst.getID())} '
                        f'fromLane="{src.getLaneNumber() - 1}" toLane="{dst.getLaneNumber() - 1}"/>'
                    )
            else:
                for dst, conns in outgoing.items():
                    if any(c.getDirection() in TURNAROUND for c in conns):
                        plan.deletions.append(f'<delete from={quoteattr(src.getID())} to={quoteattr(dst.getID())}/>')

    for i, jid in enumerate(edits.get("signals", [])):
        if not net.hasNode(jid) or not _is_intersection(net.getNode(jid)) or "," in jid:
            fail("signal", i, "unknown junction")
        elif net.getNode(jid).getType() == "traffic_light":
            fail("signal", i, "This junction already has a signal")
        else:
            plan.tls.append(jid)

    if errors:
        raise EditError(errors)
    return plan


def _variant_lock(key: str) -> threading.Lock:
    with _build_locks_guard:
        return _build_locks.setdefault(key, threading.Lock())


def _prune(area_dir: Path) -> None:
    built = sorted(
        (d for d in area_dir.iterdir() if VARIANT_ID.fullmatch(d.name)),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    for old in built[KEEP_VARIANTS:]:
        shutil.rmtree(old, ignore_errors=True)


def build_variant(area, edits: dict) -> tuple[str, list[dict]]:
    """Build (or reuse) the network for these layout edits on the area.

    Returns (variant id, resolved U-turns). Raises EditError for edits that
    don't fit the network and RuntimeError if netconvert fails.
    """
    plan = _plan(area, edits)
    vid = variant_id(edits)
    area_dir = VARIANTS_DIR / area.id
    out = area_dir / vid
    with _variant_lock(f"{area.id}/{vid}"):
        if (out / "area.json").exists():
            os.utime(out)  # recently used; keep it through pruning
            return vid, plan.resolved
        area_dir.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=area_dir, prefix=".build-"))
        try:
            _write_variant(area, plan, tmp, edits, vid)
            try:
                tmp.rename(out)
            except OSError:  # built concurrently by another process
                shutil.rmtree(tmp, ignore_errors=True)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        _prune(area_dir)
    return vid, plan.resolved


def _netconvert(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=BUILD_TIMEOUT)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-3:]
        raise RuntimeError("netconvert failed: " + " ".join(detail))


def _turn_rule_deletions(net, plan: _Plan) -> tuple[list[str], dict[str, list[str]], dict[str, str]]:
    """Connections to remove for the turn rules, on the network with crossings
    merged. Returns (deletions, merged node → its unedited nodes, unedited node → node now)."""
    deletions, joined, now = [], {}, {}
    errors = []
    for group, entry, rule, index in plan.turn_rules:
        node = net.getEdge(entry).getToNode()
        if len(group) > 1:
            joined[node.getID()] = list(group)
        for m in group:
            now[m] = node.getID()
        label, keep = TURN_RULES[rule]
        for src in node.getIncoming():
            outgoing = src.getOutgoing()
            if not _is_road(src) or not outgoing:
                continue
            kept = False
            for dst, conns in outgoing.items():
                for c in conns:
                    if MOVEMENT.get(c.getDirection(), "straight") in keep:
                        kept = True
                    else:
                        deletions.append(
                            f'<delete from={quoteattr(src.getID())} to={quoteattr(dst.getID())} '
                            f'fromLane="{c.getFromLane().getIndex()}" toLane="{c.getToLane().getIndex()}"/>'
                        )
            if not kept:
                shape = src.getShape()
                where = _compass((_bearing(shape[-2], shape[-1]) + 180) % 360)
                name = src.getName() or "the unnamed road"
                errors.append({
                    "kind": "junction_turn", "index": index,
                    "message": f"{label} leaves {name} from the {where} with no way out of this junction",
                })
    if errors:
        raise EditError(errors)
    return deletions, joined, now


def _write_variant(area, plan: _Plan, tmp: Path, edits: dict, vid: str) -> None:
    edge_lines = []
    for eid, splits in plan.splits.items():
        parts, prev = [], eid
        for s in splits:
            after = f"{eid}.{s.tag}"
            parts.append(
                f'<split pos="{s.pos:.2f}" id={quoteattr(s.node)} '
                f'idBefore={quoteattr(prev)} idAfter={quoteattr(after)}/>'
            )
            prev = after
        edge_lines.append(f"  <edge id={quoteattr(eid)}>{''.join(parts)}</edge>")
    edge_lines += [f"  {e}" for e in plan.new_edges]

    net_file = tmp / "net.net.xml"
    # Turn rules need a second pass on the network with crossings merged.
    first = tmp / "merged.net.xml" if plan.turn_rules else net_file
    cmd = ["netconvert", "--sumo-net-file", str(area.net_file), "-o", str(first), "--no-warnings"]
    if edge_lines:
        (tmp / "edits.edg.xml").write_text("<edges>\n" + "\n".join(edge_lines) + "\n</edges>\n")
        cmd += ["--edge-files", str(tmp / "edits.edg.xml")]
    if plan.connections or plan.deletions:
        lines = [f"  {c}" for c in plan.deletions + plan.connections]
        (tmp / "edits.con.xml").write_text("<connections>\n" + "\n".join(lines) + "\n</connections>\n")
        cmd += ["--connection-files", str(tmp / "edits.con.xml")]
    joins = [g for g, _, _, _ in plan.turn_rules if len(g) > 1]
    if joins:
        lines = []
        for g in joins:
            # Without a position netconvert leaves the merged node at an
            # invalid one when it edits a finished network.
            coords = [area.net.getNode(m).getCoord() for m in g]
            x = sum(c[0] for c in coords) / len(coords)
            y = sum(c[1] for c in coords) / len(coords)
            lines.append(f'  <join nodes={quoteattr(" ".join(g))} x="{x:.2f}" y="{y:.2f}"/>')
        (tmp / "edits.nod.xml").write_text("<nodes>\n" + "\n".join(lines) + "\n</nodes>\n")
        cmd += ["--node-files", str(tmp / "edits.nod.xml")]
    tls_opts = ["--tls.default-type", "actuated"]
    if plan.tls and not plan.turn_rules:
        cmd += ["--tls.set", ",".join(plan.tls), *tls_opts]
    _netconvert(cmd)

    joined: dict[str, list[str]] = {}
    if plan.turn_rules:
        merged = sumolib.net.readNet(str(first), withInternal=False)
        deletions, joined, now = _turn_rule_deletions(merged, plan)
        cmd = ["netconvert", "--sumo-net-file", str(first), "-o", str(net_file), "--no-warnings"]
        if deletions:
            (tmp / "turns.con.xml").write_text("<connections>\n" + "\n".join(f"  {d}" for d in deletions) + "\n</connections>\n")
            cmd += ["--connection-files", str(tmp / "turns.con.xml")]
        if plan.tls:
            # A new signal on a merged crossing goes on the merged junction.
            cmd += ["--tls.set", ",".join(sorted({now.get(j, j) for j in plan.tls})), *tls_opts]
        _netconvert(cmd)
        first.unlink()

    pieces = {f"{eid}.{s.tag}": eid for eid, splits in plan.splits.items() for s in splits}

    def props(edge) -> dict:
        eid = edge.getID()
        if eid in plan.connectors:
            return {"base": None, "uturn": True, "name": "U-turn"}
        return {"base": pieces.get(eid, eid)}

    stats = build_geojson(net_file, tmp / "network.geojson", props)
    meta = {**area.meta, **stats, "variant": vid, "joined": joined, "added_signals": list(plan.tls)}
    (tmp / "area.json").write_text(json.dumps(meta, indent=2))
    (tmp / "edits.json").write_text(json.dumps(_canonical(edits), indent=2, ensure_ascii=False))
