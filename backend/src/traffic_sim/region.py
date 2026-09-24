"""Region packs: everything that is specific to one city, as data.

A region pack is a folder in regions/ (see docs/regions.md):

    regions/<id>/region.toml       name, driving side, demand reference, areas
    regions/<id>/presets.toml      time-of-day scenarios
    regions/<id>/signals.toml      where signals run automatically   (optional)
    regions/<id>/waterlogging.json default flood spots                (optional)
    regions/<id>/*.typ.xml         netconvert road types              (optional)

The code reads cities only through this module, so adding or tuning a city is
a data change. Folders starting with "_" (the template) are not loaded.

    uv run traffic-sim-region list
    uv run traffic-sim-region check [region ...]
"""

import argparse
import json
import math
import re
import sys
import tomllib
from dataclasses import asdict, dataclass
from functools import cache
from pathlib import Path

from .config import AREAS_DIR, DEFAULT_REGION, REGIONS_DIR
from .vtypes import VEHICLE_TYPES_BY_ID

ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
KINDS = ("area", "city")
DETAILS = ("full", "arterial")
# A neighbourhood side longer than this simulates slowly (every street is kept).
MAX_AREA_SIDE_KM = 4.5
# A signal zone further than this from every area of its region is probably a typo.
SIGNAL_REACH_KM = 5.0
M_PER_DEG_LAT = 111_320.0


class RegionError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class AreaSpec:
    id: str
    name: str
    bbox: tuple[float, float, float, float]  # west, south, east, north
    kind: str = "area"
    detail: str = "full"


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    description: str
    start_hour: float
    volume: float  # vehicles per hour on the region's reference area
    through_share: float
    mix: dict[str, float]


@dataclass(frozen=True)
class SignalJunction:
    name: str
    lon: float
    lat: float
    radius: float  # m


@dataclass(frozen=True)
class SignalCorridor:
    name: str
    points: tuple[tuple[float, float], ...]
    buffer: float  # m


@dataclass(frozen=True)
class SignalZone:
    name: str
    polygon: tuple[tuple[float, float], ...]
    add_missing: bool


@dataclass(frozen=True)
class SignalPolicy:
    """Which signals run automatically by default; see signals.py."""

    elsewhere: str = "on"  # signals outside the listed places: "on" or "off"
    junctions: tuple[SignalJunction, ...] = ()
    corridors: tuple[SignalCorridor, ...] = ()
    zones: tuple[SignalZone, ...] = ()
    note_on: str = "Runs automatically by default."
    note_off: str = "Off by default here. Switch it on to run it automatically."


@dataclass(frozen=True)
class Region:
    id: str
    dir: Path
    name: str
    country: str
    driving_side: str  # "left" or "right"
    default_area: str
    default_preset: str
    reference_area: str
    reference_main_lane_km: float
    city_through_scale: float
    type_files: tuple[Path, ...]
    netconvert_options: tuple[str, ...]
    areas: dict[str, AreaSpec]
    presets: tuple[Preset, ...]
    signals: SignalPolicy

    @property
    def lefthand(self) -> bool:
        return self.driving_side == "left"

    @property
    def kerb_side(self) -> str:
        return self.driving_side

    @property
    def waterlogging_file(self) -> Path | None:
        f = self.dir / "waterlogging.json"
        return f if f.is_file() else None

    def preset(self, preset_id: str) -> Preset | None:
        return next((p for p in self.presets if p.id == preset_id), None)

    def api(self) -> dict:
        """What the frontend needs: names, defaults, presets and signal notes."""
        return {
            "id": self.id,
            "name": self.name,
            "country": self.country,
            "default_area": self.default_area,
            "default_preset": self.default_preset,
            "presets": [asdict(p) for p in self.presets],
            "signal_notes": {"on": self.signals.note_on, "off": self.signals.note_off},
        }


# --- parsing ---------------------------------------------------------------------


class _Reader:
    """Typed lookups into parsed TOML that collect problems instead of raising."""

    def __init__(self, problems: list[str], where: str):
        self.problems = problems
        self.where = where

    def fail(self, msg: str) -> None:
        self.problems.append(f"{self.where}: {msg}")

    def get(self, table: dict, key: str, kind, default=..., path: str = ""):
        name = f"{path}{key}"
        if key not in table:
            if default is ...:
                self.fail(f"missing '{name}'")
                return None
            return default
        v = table[key]
        ok = isinstance(v, kind) and not (isinstance(v, bool) and kind in ((int, float), float, int))
        if not ok:
            self.fail(f"'{name}' has the wrong type ({type(v).__name__})")
            return None if default is ... else default
        return v

    def number(self, table: dict, key: str, lo: float, hi: float, default=..., path: str = "") -> float | None:
        v = self.get(table, key, (int, float), default, path)
        if v is None:
            return None
        if not (math.isfinite(v) and lo <= v <= hi):
            self.fail(f"'{path}{key}' = {v} is outside {lo}..{hi}")
            return None
        return float(v)

    def choice(self, table: dict, key: str, options: tuple, default=..., path: str = "") -> str | None:
        v = self.get(table, key, str, default, path)
        if v is not None and v not in options:
            self.fail(f"'{path}{key}' = {v!r}; use one of {', '.join(options)}")
            return None
        return v

    def lonlat(self, v, what: str) -> tuple[float, float] | None:
        ok = (
            isinstance(v, list) and len(v) == 2
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)
            and -180 <= v[0] <= 180 and -90 <= v[1] <= 90
        )
        if not ok:
            self.fail(f"{what}: expected [lon, lat], got {v!r}")
            return None
        return float(v[0]), float(v[1])


def _read_toml(path: Path, problems: list[str]) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        problems.append(f"{path.name}: missing")
    except tomllib.TOMLDecodeError as e:
        problems.append(f"{path.name}: {e}")
    return {}


def _bbox(r: _Reader, v, what: str) -> tuple[float, float, float, float] | None:
    if not (isinstance(v, list) and len(v) == 4 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)):
        r.fail(f"{what}.bbox: expected [west, south, east, north], got {v!r}")
        return None
    west, south, east, north = (float(x) for x in v)
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        r.fail(f"{what}.bbox: needs west < east and south < north (lon, lat order), got {v!r}")
        return None
    return west, south, east, north


def bbox_size_km(bbox) -> tuple[float, float]:
    west, south, east, north = bbox
    lat0 = math.radians((south + north) / 2)
    return (east - west) * M_PER_DEG_LAT * math.cos(lat0) / 1000, (north - south) * M_PER_DEG_LAT / 1000


def _areas(r: _Reader, raw) -> dict[str, AreaSpec]:
    if not isinstance(raw, dict) or not raw:
        r.fail("needs at least one [areas.<id>] table")
        return {}
    out = {}
    for area_id, a in raw.items():
        what = f"areas.{area_id}"
        if not ID.fullmatch(area_id):
            r.fail(f"{what}: ids are lower-case letters, digits, '_' and '-'")
            continue
        if not isinstance(a, dict):
            r.fail(f"{what}: must be a table")
            continue
        name = r.get(a, "name", str, path=f"{what}.")
        bbox = _bbox(r, a.get("bbox"), what)
        kind = r.choice(a, "kind", KINDS, "area", f"{what}.")
        detail = r.choice(a, "detail", DETAILS, "arterial" if kind == "city" else "full", f"{what}.")
        if None in (name, bbox, kind, detail):
            continue
        out[area_id] = AreaSpec(area_id, name, bbox, kind, detail)
    return out


def _presets(r: _Reader, raw) -> tuple[Preset, ...]:
    if not isinstance(raw, list) or not raw:
        r.fail("needs at least one [[presets]] entry")
        return ()
    out, seen = [], set()
    for i, p in enumerate(raw):
        what = f"presets[{i}]"
        if not isinstance(p, dict):
            r.fail(f"{what}: must be a table")
            continue
        pid = r.get(p, "id", str, path=f"{what}.")
        if pid is not None:
            what = f"preset '{pid}'"
            if not ID.fullmatch(pid) or pid in seen:
                r.fail(f"{what}: ids must be unique, lower-case letters, digits, '_' and '-'")
            seen.add(pid)
        label = r.get(p, "label", str, path=f"{what}.")
        description = r.get(p, "description", str, "", f"{what}.")
        start = r.number(p, "start_hour", 0, 23.99, path=f"{what}.")
        volume = r.number(p, "volume", 1, 1_000_000, path=f"{what}.")
        through = r.number(p, "through_share", 0, 1, path=f"{what}.")
        mix_raw = r.get(p, "mix", dict, path=f"{what}.")
        mix = {}
        if mix_raw is not None:
            for k, v in mix_raw.items():
                if k not in VEHICLE_TYPES_BY_ID:
                    r.fail(f"{what}.mix: unknown vehicle type '{k}' (known: {', '.join(VEHICLE_TYPES_BY_ID)})")
                elif not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
                    r.fail(f"{what}.mix.{k}: must be a number >= 0")
                else:
                    mix[k] = float(v)
            if mix_raw and sum(mix.values()) <= 0:
                r.fail(f"{what}.mix: weights add up to 0")
        if None in (pid, label, start, volume, through, mix_raw):
            continue
        out.append(Preset(pid, label, description, start, volume, through, mix))
    return tuple(out)


def _points(r: _Reader, raw, what: str, at_least: int) -> tuple[tuple[float, float], ...] | None:
    if not isinstance(raw, list) or len(raw) < at_least:
        r.fail(f"{what}: needs at least {at_least} [lon, lat] points")
        return None
    pts = [r.lonlat(p, what) for p in raw]
    return None if None in pts else tuple(pts)


def _signals(r: _Reader, raw: dict) -> SignalPolicy:
    if not raw:
        return SignalPolicy()
    base = SignalPolicy()
    elsewhere = r.choice(raw, "elsewhere", ("on", "off"), "on")
    junctions = []
    for i, j in enumerate(r.get(raw, "junctions", list, [])):
        what = f"junctions[{i}]"
        if not isinstance(j, dict):
            r.fail(f"{what}: must be a table")
            continue
        name = r.get(j, "name", str, path=f"{what}.")
        lon = r.number(j, "lon", -180, 180, path=f"{what}.")
        lat = r.number(j, "lat", -90, 90, path=f"{what}.")
        radius = r.number(j, "radius", 5, 500, path=f"{what}.")
        if None not in (name, lon, lat, radius):
            junctions.append(SignalJunction(name, lon, lat, radius))
    corridors = []
    for i, c in enumerate(r.get(raw, "corridors", list, [])):
        what = f"corridors[{i}]"
        name = r.get(c, "name", str, path=f"{what}.")
        buffer = r.number(c, "buffer", 5, 1000, path=f"{what}.")
        points = _points(r, c.get("points"), f"{what}.points", 2)
        if None not in (name, buffer, points):
            corridors.append(SignalCorridor(name, points, buffer))
    zones = []
    for i, z in enumerate(r.get(raw, "zones", list, [])):
        what = f"zones[{i}]"
        name = r.get(z, "name", str, path=f"{what}.")
        polygon = _points(r, z.get("polygon"), f"{what}.polygon", 3)
        add = r.get(z, "add_missing", bool, False, f"{what}.")
        if None not in (name, polygon):
            zones.append(SignalZone(name, polygon, add))
    names = [x.name for x in (*junctions, *zones)]
    for n in {n for n in names if names.count(n) > 1}:
        r.fail(f"'{n}' names two junctions or zones; names become signal ids")
    for n in names:
        if not re.fullmatch(r"[A-Za-z0-9_]+", n):
            r.fail(f"'{n}': junction and zone names are letters, digits and '_' (they become signal ids)")
    return SignalPolicy(
        elsewhere or "on", tuple(junctions), tuple(corridors), tuple(zones),
        r.get(raw, "note_on", str, base.note_on), r.get(raw, "note_off", str, base.note_off),
    )


def parse_region(region_dir: Path) -> tuple[Region | None, list[str], list[str]]:
    """(region or None, problems, warnings) for one pack folder."""
    problems: list[str] = []
    warnings: list[str] = []
    region_id = region_dir.name
    if not ID.fullmatch(region_id):
        return None, [f"folder name '{region_id}': use lower-case letters, digits, '_' and '-'"], []

    meta = _read_toml(region_dir / "region.toml", problems)
    presets_raw = _read_toml(region_dir / "presets.toml", problems)
    signals_file = region_dir / "signals.toml"
    signals_raw = _read_toml(signals_file, problems) if signals_file.exists() else {}
    if problems:
        return None, problems, warnings

    r = _Reader(problems, "region.toml")
    name = r.get(meta, "name", str)
    country = r.get(meta, "country", str, "")
    side = r.choice(meta, "driving_side", ("left", "right"))
    areas = _areas(r, meta.get("areas"))
    default_area = r.get(meta, "default_area", str)
    if default_area is not None and areas and default_area not in areas:
        r.fail(f"default_area '{default_area}' is not in [areas]")
    demand = r.get(meta, "demand", dict)
    ref_area = ref_km = through = None
    if demand is not None:
        ref_area = r.get(demand, "reference_area", str, path="demand.")
        ref_km = r.number(demand, "reference_main_lane_km", 0.1, 100_000, path="demand.")
        through = r.number(demand, "city_through_scale", 0, 1, 1.0, "demand.")
        if ref_area is not None and areas and ref_area not in areas:
            r.fail(f"demand.reference_area '{ref_area}' is not in [areas]")
    network = r.get(meta, "network", dict, {})
    type_files = []
    for f in r.get(network, "type_files", list, [], "network."):
        if not isinstance(f, str) or not (region_dir / f).is_file():
            r.fail(f"network.type_files: '{f}' is not a file in the region folder")
        else:
            type_files.append(region_dir / f)
    options = r.get(network, "netconvert_options", list, [], "network.")
    if not all(isinstance(o, str) for o in options):
        r.fail("network.netconvert_options: must be strings")
    for area in areas.values():
        w, h = bbox_size_km(area.bbox)
        if area.kind == "area" and max(w, h) > MAX_AREA_SIDE_KM:
            warnings.append(
                f"region.toml: area '{area.id}' is {w:.1f} × {h:.1f} km; neighbourhoods over "
                f"{MAX_AREA_SIDE_KM} km a side simulate slowly (or use kind = \"city\")"
            )

    pr = _Reader(problems, "presets.toml")
    presets = _presets(pr, presets_raw.get("presets"))
    default_preset = r.get(meta, "default_preset", str)
    if default_preset is not None and presets and not any(p.id == default_preset for p in presets):
        r.fail(f"default_preset '{default_preset}' is not in presets.toml")
    for p in presets:
        missing = [k for k in VEHICLE_TYPES_BY_ID if k not in p.mix]
        if missing:
            warnings.append(f"presets.toml: preset '{p.id}' has no {', '.join(missing)} (share 0)")

    signals = _signals(_Reader(problems, "signals.toml"), signals_raw)
    if areas:
        places = [(j.name, j.lon, j.lat) for j in signals.junctions]
        places += [(c.name, *c.points[0]) for c in signals.corridors]
        places += [(z.name, *z.polygon[0]) for z in signals.zones]
        for pname, lon, lat in places:
            if not any(_near_bbox(lon, lat, a.bbox, SIGNAL_REACH_KM) for a in areas.values()):
                warnings.append(f"signals.toml: '{pname}' is more than {SIGNAL_REACH_KM:.0f} km from every area (lon/lat swapped?)")

    wl = region_dir / "waterlogging.json"
    if wl.exists():
        _check_waterlogging(wl, problems)

    if problems:
        return None, problems, warnings
    region = Region(
        region_id, region_dir, name, country, side, default_area, default_preset, ref_area, ref_km,
        through, tuple(type_files), tuple(options), areas, presets, signals,
    )
    return region, problems, warnings


def _near_bbox(lon: float, lat: float, bbox, km: float) -> bool:
    west, south, east, north = bbox
    pad_lat = km * 1000 / M_PER_DEG_LAT
    pad_lon = pad_lat / max(0.1, math.cos(math.radians(lat)))
    return west - pad_lon <= lon <= east + pad_lon and south - pad_lat <= lat <= north + pad_lat


def _check_waterlogging(path: Path, problems: list[str]) -> None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        problems.append(f"waterlogging.json: {e}")
        return
    sources = doc.get("sources") if isinstance(doc, dict) else None
    spots = doc.get("spots") if isinstance(doc, dict) else None
    if not isinstance(sources, dict) or not isinstance(spots, list) or not isinstance(doc.get("about"), str):
        problems.append("waterlogging.json: needs \"about\" (text), \"sources\" (object) and \"spots\" (list)")
        return
    for i, s in enumerate(spots):
        ok = (
            isinstance(s, dict) and isinstance(s.get("id"), str) and isinstance(s.get("name"), str)
            and all(isinstance(s.get(k), (int, float)) for k in ("lon", "lat", "radius"))
            and s.get("depth") in ("shallow", "knee", "deep")
        )
        if not ok:
            problems.append(f"waterlogging.json: spots[{i}] needs id, name, lon, lat, radius and depth (shallow|knee|deep)")
            continue
        unknown = [k for k in s.get("sources", []) if k not in sources]
        if not s.get("sources") or unknown:
            problems.append(f"waterlogging.json: spot '{s['id']}' needs sources that are keys of \"sources\"")


# --- loading ---------------------------------------------------------------------


def region_dirs(root: Path = REGIONS_DIR) -> list[Path]:
    return sorted(d for d in root.iterdir() if d.is_dir() and not d.name.startswith(("_", ".")))


@cache
def all_regions() -> dict[str, Region]:
    """Every region pack. RegionError (listing every problem) if any is invalid."""
    out, problems = {}, []
    owner: dict[str, str] = {}
    for d in region_dirs():
        region, errs, _ = parse_region(d)
        problems += [f"regions/{d.name}/{e}" for e in errs]
        if region is None:
            continue
        for area_id in region.areas:
            if area_id in owner:
                problems.append(f"regions/{d.name}/region.toml: area id '{area_id}' is also in region '{owner[area_id]}'")
            owner[area_id] = region.id
        out[region.id] = region
    if problems:
        raise RegionError(problems)
    if not out:
        raise RegionError([f"no region packs in {REGIONS_DIR}"])
    return out


def get_region(region_id: str) -> Region:
    """KeyError if there's no such pack."""
    return all_regions()[region_id]


def default_region() -> Region:
    regions = all_regions()
    return regions.get(DEFAULT_REGION) or next(iter(regions.values()))


def find_area(area_id: str) -> tuple[Region, AreaSpec] | None:
    for region in all_regions().values():
        if area_id in region.areas:
            return region, region.areas[area_id]
    return None


def region_for(meta: dict) -> Region:
    """The region a prepared area belongs to. KeyError if its pack is gone.

    Areas prepared before region packs existed have no "region"; they belong
    to the pack that lists them, else the default region.
    """
    if meta.get("region"):
        return get_region(meta["region"])
    found = find_area(meta.get("id", ""))
    return found[0] if found else default_region()


def area_info(meta: dict) -> dict:
    """An area.json with the values its region decides, computed now so that
    tuning a region needs no rebuild."""
    region = region_for(meta)
    out = {**meta, "region": region.id, "city": meta.get("city") or region.name}
    if "main_lane_km" in meta:
        out["demand_scale"] = round(max(0.1, meta["main_lane_km"] / region.reference_main_lane_km), 2)
    out["through_scale"] = region.city_through_scale if meta.get("kind") == "city" else 1.0
    return out


def preset_demand(preset: Preset, info: dict) -> dict:
    """A preset scaled to an area (as the frontend's presetDemand does)."""
    volume = preset.volume * info.get("demand_scale", 1)
    step = 1000 if volume > 20000 else 50
    return {
        "volume": round(volume / step) * step,
        "mix": dict(preset.mix),
        "through_share": min(1.0, round(preset.through_share * info.get("through_scale", 1) * 100) / 100),
    }


# --- CLI ---------------------------------------------------------------------------


def _built(area_id: str) -> dict | None:
    f = AREAS_DIR / area_id / "area.json"
    return json.loads(f.read_text()) if f.exists() else None


def _check(ids: list[str]) -> int:
    dirs = region_dirs()
    known = {d.name for d in dirs}
    unknown = [i for i in ids if i not in known]
    if unknown:
        print(f"no region pack: {', '.join(unknown)} (have: {', '.join(sorted(known))})")
        return 1
    failed = False
    owner: dict[str, str] = {}
    for d in dirs:
        region, problems, warnings = parse_region(d)
        if region:
            for area_id in region.areas:
                if area_id in owner:
                    problems.append(f"region.toml: area id '{area_id}' is also in region '{owner[area_id]}'")
                owner[area_id] = region.id
        if ids and d.name not in ids:
            continue
        if region and not problems:
            ref = _built(region.reference_area)
            if ref and "main_lane_km" in ref:
                actual = ref["main_lane_km"]
                if abs(actual - region.reference_main_lane_km) > 0.05 * actual:
                    warnings.append(
                        f"region.toml: demand.reference_main_lane_km is {region.reference_main_lane_km}, "
                        f"but the built '{region.reference_area}' has {actual}; set it to {actual}"
                    )
            elif not ref:
                warnings.append(
                    f"reference area '{region.reference_area}' isn't built; build it and copy its "
                    "main_lane_km into demand.reference_main_lane_km"
                )
        status = "FAIL" if problems else "ok"
        print(f"{d.name}: {status}")
        for p in problems:
            print(f"  error: {p}")
        for w in warnings:
            print(f"  warning: {w}")
        failed |= bool(problems)
    return 1 if failed else 0


def _list() -> int:
    for region in all_regions().values():
        default = " (default)" if region.id == default_region().id else ""
        print(f"{region.id}{default}: {region.name}, {region.country}; drives on the {region.driving_side}")
        for a in region.areas.values():
            built = _built(a.id)
            w, h = bbox_size_km(a.bbox)
            state = f"built, {built.get('edges', '?')} edges" if built else "not built"
            print(f"  {a.id:<14} {a.kind:<4} {w:4.1f} × {h:3.1f} km  {state}  {a.name}")
        print(f"  presets: {', '.join(p.id for p in region.presets)}")
    return 0


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="regions, their areas and which are built")
    c = sub.add_parser("check", help="validate region packs (all, or the ones named)")
    c.add_argument("regions", nargs="*")
    args = p.parse_args(argv)
    if args.cmd == "check":
        sys.exit(_check(args.regions))
    try:
        sys.exit(_list())
    except RegionError as e:
        sys.exit(f"invalid region packs (run `traffic-sim-region check`):\n{e}")
