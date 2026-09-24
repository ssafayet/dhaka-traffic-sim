"""Things on and around the road that users place on the map, plus weather.

All of it applies live to a running simulation (see Simulation.set_features):

* Bus stops: buses whose route passes one stop in the kerb lane.
* Rickshaw / CNG stands: vehicles parked at the kerb, and passing ones of that
  kind stopping to pick up fares.
* Pedestrian crossing spots: every so often people cross and hold up all
  lanes for a few seconds (SUMO's own pedestrians only use marked crossings).
* Hot zones (markets, schools, terminals): more trips start and end there,
  vehicles stop at the kerb more often, vendors can take the kerb lane and
  crowds can slow traffic.
* Waterlogging zones: when flooded, water slows traffic or keeps some
  vehicles out, by depth.
* Weather, breakdowns and how much travellers avoid delay.

Points arrive as lon/lat and are snapped to the nearest suitable road. The
defaults are the bus stops in the area's OpenStreetMap data and the
waterlogging spots in the region's waterlogging.json (from news reports).
"""

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_POINTS = 500  # of each kind
MAX_ZONES = 200
SNAP_RADIUS = 40.0  # m from a click to the road it belongs to
MAX_ZONE_RADIUS = 1500.0  # m
MAX_SECONDS = 3600

STAND_KINDS = ("e_rickshaw", "rickshaw", "cng")
# Vehicle types that stop at the kerb for passengers.
KERB_STOPPERS = ("bus", "cng", "e_rickshaw", "rickshaw")
RAIN = ("none", "light", "heavy")
FLOODING = ("auto", "on", "off")  # auto: with heavy rain

# Rain: drivers go slower and keep longer gaps (speed factor, headway factor).
RAIN_EFFECT = {"none": (1.0, 1.0), "light": (0.9, 1.25), "heavy": (0.75, 1.6)}

# Water depth → speed cap (m/s) and SUMO vehicle classes that can't get
# through. Cars, CNGs and motorbikes stall in deep water; buses, trucks and
# rickshaws keep going, slowly. Rickshaw classes (moped / bicycle) aren't
# banned: battery and pedal rickshaws can't be told apart by class.
WATER_DEPTH = {
    "shallow": (20 / 3.6, frozenset()),
    "knee": (10 / 3.6, frozenset({"motorcycle", "taxi"})),
    "deep": (5 / 3.6, frozenset({"motorcycle", "taxi", "passenger"})),
}
# Hot zone crowds: traffic moves at this share of the speed limit.
CROWD_SLOWDOWN = 0.6
# Kerb lane taken by vendors on a one-lane road: speed cap instead.
VENDOR_SPEED_ONE_LANE = 15 / 3.6

_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")


@dataclass(frozen=True)
class Spot:
    """A point snapped to a road: edge id and position along it (m)."""

    edge: str
    pos: float
    twin: str | None = None  # the other direction of a two-way street
    twin_pos: float = 0.0


@dataclass(frozen=True)
class BusStop:
    id: str
    at: Spot
    dwell: float  # s, average


@dataclass(frozen=True)
class Stand:
    id: str
    at: Spot
    kind: str  # a vehicle type id, see STAND_KINDS
    parked: int  # vehicles parked at the kerb
    pickup: float  # 0..1: share of passing vehicles of this kind that stop
    dwell: float


@dataclass(frozen=True)
class Crossing:
    id: str
    at: Spot
    every: float  # s between groups crossing, on average
    duration: float  # s the road is held up


@dataclass(frozen=True)
class HotZone:
    id: str
    edges: frozenset[str]
    trips: float  # multiplier on trips starting and ending here
    kerb_stops: float  # 0..1: chance a bus / CNG / rickshaw stops once in the zone
    vendors: bool  # kerb lane taken by vendors
    crowds: bool  # pedestrians slow traffic


@dataclass(frozen=True)
class WaterZone:
    id: str
    edges: frozenset[str]
    depth: str


@dataclass(frozen=True)
class Features:
    bus_stops: tuple[BusStop, ...] = ()
    stands: tuple[Stand, ...] = ()
    crossings: tuple[Crossing, ...] = ()
    hot_zones: tuple[HotZone, ...] = ()
    water: tuple[WaterZone, ...] = ()
    rain: str = "none"
    flooding: str = "auto"
    breakdowns: float = 0.0  # per hour per 100 km of road
    elasticity: float = 0.0  # share of trips not made per 10 min of average delay
    warnings: tuple[str, ...] = field(default=(), compare=False)

    @property
    def flooded(self) -> bool:
        return self.flooding == "on" or (self.flooding == "auto" and self.rain == "heavy")


# --- parsing ---------------------------------------------------------------------


def _num(raw: dict, key: str, lo: float, hi: float, default: float) -> float:
    v = raw.get(key, default)
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
        raise ValueError(f"{key} must be a number")
    return float(min(hi, max(lo, v)))


def _lonlat(raw: dict) -> tuple[float, float]:
    lon, lat = raw.get("lon"), raw.get("lat")
    ok = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (lon, lat))
    if not ok or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError("every feature needs lon and lat")
    return float(lon), float(lat)


def _items(raw, key: str, limit: int) -> list[dict]:
    items = raw.get(key) or []
    if not isinstance(items, list) or len(items) > limit or not all(isinstance(i, dict) for i in items):
        raise ValueError(f"{key} must be a list of at most {limit} objects")
    return items


def _id(item: dict) -> str:
    v = item.get("id")
    if not isinstance(v, str) or not _ID.fullmatch(v):
        raise ValueError("every feature needs a short id")
    return v


# Road lookups are slow on a big network (no spatial index), and a features
# update repeats nearly all of the previous one; remember them per area.
_CACHE_MAX = 5000


def _cached(area, key, compute):
    cache = area.__dict__.setdefault("_feature_cache", {})
    if key not in cache:
        if len(cache) >= _CACHE_MAX:
            cache.clear()
        cache[key] = compute()
    return cache[key]


def snap(area, lon: float, lat: float, vclass: str | None = None) -> Spot | None:
    """The road nearest to a point (that `vclass` may use), and where on it."""
    return _cached(area, ("snap", round(lon, 7), round(lat, 7), vclass), lambda: _snap(area, lon, lat, vclass))


def _snap(area, lon: float, lat: float, vclass: str | None) -> Spot | None:
    from sumolib.geomhelper import polygonOffsetWithMinimumDistanceToPoint

    net = area.net
    x, y = net.convertLonLat2XY(lon, lat)
    best = None
    for edge, dist in net.getNeighboringEdges(x, y, SNAP_RADIUS):
        if edge.getFunction() or (vclass and not edge.allows(vclass)):
            continue
        if best is None or dist < best[1]:
            best = (edge, dist)
    if best is None:
        return None
    edge = best[0]

    def along(e):
        shape = e.getShape()
        length = sum(math.dist(a, b) for a, b in zip(shape, shape[1:])) or 1
        off = polygonOffsetWithMinimumDistanceToPoint((x, y), shape)
        return min(max(1.0, off * e.getLength() / length), max(1.0, e.getLength() - 1))

    twin_id = area.twins.get(edge.getID())
    twin = net.getEdge(twin_id) if twin_id else None
    return Spot(edge.getID(), along(edge), twin_id, along(twin) if twin else 0.0)


def zone_edges(area, lon: float, lat: float, radius: float) -> frozenset[str]:
    """Roads within `radius` metres of a point."""
    return _cached(area, ("zone", round(lon, 7), round(lat, 7), radius), lambda: _zone_edges(area, lon, lat, radius))


def _zone_edges(area, lon: float, lat: float, radius: float) -> frozenset[str]:
    net = area.net
    x, y = net.convertLonLat2XY(lon, lat)
    return frozenset(e.getID() for e, _ in net.getNeighboringEdges(x, y, radius) if not e.getFunction())


def parse_features(area, raw) -> Features:
    """Validate the client's features and place them on the area's roads.

    Points that aren't near a suitable road are left out, with a warning.
    Raises ValueError for malformed input.
    """
    if raw is None:
        return Features()
    if not isinstance(raw, dict):
        raise ValueError("features must be an object")
    warnings: list[str] = []

    def place(item, vclass, what):
        spot = snap(area, *_lonlat(item), vclass)
        if spot is None:
            warnings.append(f"A {what} isn't next to a road it can use, so it was left out.")
        return spot

    bus_stops = []
    for item in _items(raw, "bus_stops", MAX_POINTS):
        spot = place(item, "bus", "bus stop")
        if spot:
            bus_stops.append(BusStop(_id(item), spot, _num(item, "dwell", 5, 600, 30)))

    stands = []
    for item in _items(raw, "stands", MAX_POINTS):
        kind = item.get("kind", "e_rickshaw")
        if kind not in STAND_KINDS:
            raise ValueError(f"stand kind must be one of {', '.join(STAND_KINDS)}")
        spot = place(item, None, "stand")
        if spot:
            stands.append(Stand(
                _id(item), spot, kind, int(_num(item, "parked", 0, 20, 3)),
                _num(item, "pickup", 0, 1, 0.3), _num(item, "dwell", 5, 600, 20),
            ))

    crossings = []
    for item in _items(raw, "crossings", MAX_POINTS):
        spot = place(item, None, "crossing")
        if spot:
            crossings.append(Crossing(
                _id(item), spot, _num(item, "every", 10, MAX_SECONDS, 60), _num(item, "duration", 2, 120, 10),
            ))

    hot_zones = []
    for item in _items(raw, "hot_zones", MAX_ZONES):
        lon, lat = _lonlat(item)
        edges = zone_edges(area, lon, lat, _num(item, "radius", 20, MAX_ZONE_RADIUS, 200))
        hot_zones.append(HotZone(
            _id(item), edges, _num(item, "trips", 1, 10, 3), _num(item, "kerb_stops", 0, 1, 0.3),
            bool(item.get("vendors", False)), bool(item.get("crowds", False)),
        ))

    water = []
    for item in _items(raw, "water", MAX_ZONES):
        depth = item.get("depth", "shallow")
        if depth not in WATER_DEPTH:
            raise ValueError(f"water depth must be one of {', '.join(WATER_DEPTH)}")
        lon, lat = _lonlat(item)
        water.append(WaterZone(_id(item), zone_edges(area, lon, lat, _num(item, "radius", 20, MAX_ZONE_RADIUS, 200)), depth))

    weather = raw.get("weather") or {}
    if not isinstance(weather, dict):
        raise ValueError("weather must be an object")
    rain, flooding = weather.get("rain", "none"), weather.get("flooding", "auto")
    if rain not in RAIN or flooding not in FLOODING:
        raise ValueError(f"rain must be one of {', '.join(RAIN)}; flooding one of {', '.join(FLOODING)}")

    return Features(
        tuple(bus_stops), tuple(stands), tuple(crossings), tuple(hot_zones), tuple(water),
        rain, flooding, _num(raw, "breakdowns", 0, 50, 0), _num(raw, "elasticity", 0, 1, 0), tuple(warnings),
    )


# --- defaults ----------------------------------------------------------------------


def _osm_bus_stops(area_dir: Path) -> list[dict]:
    """Bus stops tagged on the roads in the area's OpenStreetMap download."""
    osm = area_dir / "map.osm"
    if not osm.exists():
        return []
    import xml.etree.ElementTree as ET

    stops = []
    for _, el in ET.iterparse(osm, events=("end",)):
        if el.tag == "node":
            tags = {t.get("k"): t.get("v") for t in el.findall("tag")}
            if tags.get("highway") == "bus_stop":
                stops.append({
                    "id": f"osm{el.get('id')}",
                    "lon": round(float(el.get("lon")), 6),
                    "lat": round(float(el.get("lat")), 6),
                    "name": tags.get("name:en") or tags.get("name") or "",
                })
            el.clear()
        elif el.tag == "way":
            el.clear()
    return stops


def defaults(area, base_dir: Path) -> dict:
    """Bus stops from OpenStreetMap and news-reported waterlogging spots in the area."""
    west, south, east, north = area.meta["bbox"]
    pad = 0.003  # ~300 m: zones just outside the box still reach its roads
    wl = area.region.waterlogging_file
    doc = json.loads(wl.read_text()) if wl else {"about": "", "sources": {}, "spots": []}
    water = [
        {**s, "sources": [doc["sources"][k] for k in s["sources"]]}
        for s in doc["spots"]
        if west - pad <= s["lon"] <= east + pad and south - pad <= s["lat"] <= north + pad
    ]
    cache = base_dir / "bus_stops.json"
    if cache.exists():
        stops = json.loads(cache.read_text())
    else:
        stops = _osm_bus_stops(base_dir)
        if (base_dir / "map.osm").exists():
            cache.write_text(json.dumps(stops, ensure_ascii=False))
    return {"bus_stops": stops, "water": water, "water_about": doc["about"]}
