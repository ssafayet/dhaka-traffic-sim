"""Live demand generation.

Instead of pre-computing a route file, vehicles are injected every step via
TraCI so the demand and mix sliders take effect immediately while the
simulation runs.

A share of trips are *through traffic*: they enter at the edge of the area and
leave at the edge (a small area of Dhaka mostly carries traffic that is just
passing through). The rest start or end on a random street inside the area,
weighted by lane-km so main roads get more trips than alleys.
"""

import random
from dataclasses import dataclass, field

import sumolib

from .vtypes import VEHICLE_TYPES


@dataclass
class DemandSettings:
    volume: float = 6000  # vehicles entering per hour
    mix: dict[str, float] = field(
        default_factory=lambda: {vt.id: vt.default_share for vt in VEHICLE_TYPES}
    )
    through_share: float = 0.6  # 0..1

    def shares(self) -> dict[str, float]:
        total = sum(max(0.0, w) for w in self.mix.values())
        if total <= 0:
            return {k: 0.0 for k in self.mix}
        return {k: max(0.0, w) / total for k, w in self.mix.items()}


def _largest_strongly_connected(succ: dict[str, list[str]]) -> set[str]:
    """Edges of the largest set where every edge can reach every other."""
    allowed = succ
    # Iterative Tarjan.
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    best: list[str] = []
    counter = 0
    for root in allowed:
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            v, i = work.pop()
            if i == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on_stack.add(v)
            recurse = False
            for j in range(i, len(succ[v])):
                w = succ[v][j]
                if w not in index:
                    work.append((v, j + 1))
                    work.append((w, 0))
                    recurse = True
                    break
                if w in on_stack:
                    low[v] = min(low[v], index[w])
            if recurse:
                continue
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > len(best):
                    best = comp
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
    return set(best)


def _reachable(start: set[str], succ: dict[str, list[str]]) -> set[str]:
    seen = set(start)
    todo = list(start)
    while todo:
        for w in succ[todo.pop()]:
            if w not in seen:
                seen.add(w)
                todo.append(w)
    return seen


def routable_sets(edges: list, vclass: str) -> tuple[set[str], set[str]]:
    """(possible origins, possible destinations) such that any origin can reach
    any destination.

    Some roads ban rickshaws and OSM has one-way dead ends, so the drivable
    network differs per vehicle class. Trips go origin → core → destination,
    where the core is the largest strongly connected part of the network.
    """
    ids = {e.getID() for e in edges}
    succ = {
        e.getID(): [t.getID() for t in e.getAllowedOutgoing(vclass) if t.getID() in ids]
        for e in edges
    }
    pred: dict[str, list[str]] = {eid: [] for eid in ids}
    for eid, outs in succ.items():
        for w in outs:
            pred[w].append(eid)
    core = _largest_strongly_connected(succ)
    return _reachable(core, pred), _reachable(core, succ)


def _is_boundary_node(node, bbox, margin: float = 100.0) -> bool:
    """A dead end (one neighbour) where a road leaves the simulated area.

    Roads crossing the area border are kept whole, so their cut-off ends lie
    outside (or just inside) the area's bounding box.
    """
    neighbours = {e.getFromNode().getID() for e in node.getIncoming()}
    neighbours |= {e.getToNode().getID() for e in node.getOutgoing()}
    if len(neighbours) > 1:
        return False
    (xmin, ymin), (xmax, ymax) = bbox
    x, y = node.getCoord()
    return min(x - xmin, xmax - x, y - ymin, ymax - y) < margin


class EdgePools:
    """Weighted origin/destination edges for one vehicle class on one network.

    Building these runs a connectivity analysis over the whole network, so they
    are built once per (area, vClass) and shared by every simulation.
    """

    def __init__(self, edges: list, vclass: str, bbox):
        can_start, can_end = routable_sets(edges, vclass)
        starts = [e for e in edges if e.getID() in can_start]
        ends = [e for e in edges if e.getID() in can_end]
        self.origins = [e.getID() for e in starts]
        self.origins_w = [e.getLength() * e.getLaneNumber() for e in starts]
        self.dests = [e.getID() for e in ends]
        self.dests_w = [e.getLength() * e.getLaneNumber() for e in ends]
        # Where roads are cut off by the area boundary; bigger roads carry more.
        src = [e for e in starts if _is_boundary_node(e.getFromNode(), bbox)]
        dst = [e for e in ends if _is_boundary_node(e.getToNode(), bbox)]
        self.sources = [e.getID() for e in src]
        self.sources_w = [(e.getLaneNumber() * e.getSpeed()) ** 2 for e in src]
        self.sinks = [e.getID() for e in dst]
        self.sinks_w = [(e.getLaneNumber() * e.getSpeed()) ** 2 for e in dst]

    @classmethod
    def build(cls, net: sumolib.net.Net, vclass: str, bbox) -> "EdgePools | None":
        edges = [
            e for e in net.getEdges()
            if not e.getFunction() and e.allows(vclass) and e.getLength() > 5
        ]
        pools = cls(edges, vclass, bbox) if edges else None
        return pools if pools and pools.origins and pools.dests else None

    def origin(self, rng: random.Random, through: bool, weights: list[float] | None = None) -> str:
        if through and self.sources:
            return rng.choices(self.sources, self.sources_w)[0]
        return rng.choices(self.origins, weights or self.origins_w)[0]

    def destination(self, rng: random.Random, through: bool, weights: list[float] | None = None) -> str:
        if through and self.sinks:
            return rng.choices(self.sinks, self.sinks_w)[0]
        return rng.choices(self.dests, weights or self.dests_w)[0]


class DemandGenerator:
    def __init__(self, pools: dict[str, EdgePools | None], seed: int | None = None):
        """pools: vehicle type id → where that type can drive (None = nowhere)."""
        self.rng = random.Random(seed)
        self.pools = {k: v for k, v in pools.items() if v is not None}
        # Per-run weights for local trips (hot zones); the pools are shared.
        self._weights: dict[str, tuple[list[float], list[float]]] = {}

    def set_edge_weights(self, multiplier: dict[str, float]) -> None:
        """Make local trips start and end on these edges `multiplier` times as often."""
        self._weights = {}
        if not multiplier:
            return
        for k, pool in self.pools.items():
            self._weights[k] = (
                [w * multiplier.get(e, 1.0) for e, w in zip(pool.origins, pool.origins_w)],
                [w * multiplier.get(e, 1.0) for e, w in zip(pool.dests, pool.dests_w)],
            )

    def _poisson(self, lam: float) -> int:
        # Knuth; lam is small (vehicles per step per type).
        if lam <= 0:
            return 0
        if lam > 30:
            return max(0, round(self.rng.gauss(lam, lam**0.5)))
        l, k, p = pow(2.718281828459045, -lam), 0, 1.0
        while True:
            p *= self.rng.random()
            if p <= l:
                return k
            k += 1

    def trips_for_step(self, settings: DemandSettings, dt: float):
        """Yield (vtype_id, origin_edge, dest_edge, is_through) for this step."""
        per_step = settings.volume / 3600.0 * dt
        rng = self.rng
        for vtype, share in settings.shares().items():
            pool = self.pools.get(vtype)
            if pool is None:
                continue
            ow, dw = self._weights.get(vtype, (None, None))
            for _ in range(self._poisson(per_step * share)):
                through = rng.random() < settings.through_share
                o = pool.origin(rng, through, ow)
                d = pool.destination(rng, through, dw)
                if o != d:
                    yield vtype, o, d, through
