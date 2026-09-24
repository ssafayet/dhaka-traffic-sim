"""Build a simulation area from OpenStreetMap.

    uv run traffic-sim-prepare farmgate mirpur      # areas listed in a region pack
    uv run traffic-sim-prepare --missing            # every listed area not built yet
    uv run traffic-sim-prepare --all --region dhaka # rebuild one region's areas
    uv run traffic-sim-prepare pallabi --name "Pallabi" --bbox 90.355,23.815,90.375,23.830

Areas are listed in regions/<region>/region.toml; the region also decides the
driving side, road types and automatic signals. Steps: download OSM (Overpass)
→ netconvert → automatic signals (signals.py) → network.geojson for the map.
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

import sumolib
import sumo

from .config import AREAS_DIR, VARIANTS_DIR
from .geo import NetProjection
from .region import AreaSpec, Region, RegionError, all_regions, default_region, find_area, get_region
from .signals import add_automated_signals

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Road classes that carry traffic. Footways, paths and service roads (parking
# aisles, driveways) are dropped so they don't become phantom roads. A whole
# city at full detail is ~100k road segments, too many to simulate live, so the
# city network keeps only the main road grid.
ARTERIAL_ROADS = (
    "motorway|trunk|primary|secondary|tertiary|"
    "motorway_link|trunk_link|primary_link|secondary_link|tertiary_link"
)
DETAIL_FILTERS = {
    "full": ARTERIAL_ROADS + "|unclassified|residential|living_street",
    "arterial": ARTERIAL_ROADS,
}

# Types that count as "main roads" for demand scaling: an area's main-road
# lane-km over its region's reference area's scales the presets (region.area_info).
MAIN_ROAD_TYPES = {f"highway.{t}" for t in ("trunk", "primary", "secondary", "tertiary")}

SUMO_TYPES = Path(sumo.SUMO_HOME) / "data" / "typemap" / "osmNetconvert.typ.xml"

NETCONVERT_OPTIONS = [
    "--geometry.remove",
    "--roundabouts.guess",
    "--ramps.guess",
    "--junctions.join",
    "--junctions.corner-detail", "5",
    "--tls.guess-signals",
    "--tls.discard-simple",
    "--tls.join",
    "--tls.default-type", "actuated",
    "--remove-edges.isolated",
    "--keep-edges.components", "1",  # only the largest connected road network
    "--keep-edges.by-vclass", "passenger,bus,taxi,bicycle,motorcycle,truck",
    "--osm.sidewalks", "false",
    "--output.street-names",
    "--output.original-names",
    "--no-warnings",
]


def download_osm(bbox: tuple[float, float, float, float], out: Path, detail: str = "full") -> None:
    west, south, east, north = bbox
    query = f"""
    [out:xml][timeout:300];
    way["highway"~"^({DETAIL_FILTERS[detail]})$"]({south},{west},{north},{east});
    (._;>;);
    out body;
    """
    data = urllib.parse.urlencode({"data": query}).encode()
    last_err: Exception | None = None
    for url in OVERPASS_URLS:
        for attempt in range(3):
            try:
                print(f"  downloading OSM from {url} ...")
                req = urllib.request.Request(
                    url, data=data, headers={"User-Agent": "traffic-sim/0.1"}
                )
                with urllib.request.urlopen(req, timeout=400) as resp:
                    body = resp.read()
                if b"<osm" not in body[:500]:
                    raise RuntimeError(body[:300].decode(errors="replace"))
                out.write_bytes(body)
                print(f"  saved {len(body) / 1e6:.1f} MB → {out}")
                return
            except Exception as e:  # noqa: BLE001 — try the next mirror
                last_err = e
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"OSM download failed: {last_err}")


def build_net(osm_file: Path, net_file: Path, bbox: tuple, region: Region) -> None:
    # Overpass returns whole ways, which can run far outside the box; crop them.
    crop = ["--keep-edges.in-geo-boundary", ",".join(str(v) for v in bbox)]
    # The region's type files load after SUMO's and replace the types they list.
    types = ["--type-files", ",".join(str(f) for f in (SUMO_TYPES, *region.type_files))]
    side = ["--lefthand"] if region.lefthand else []
    cmd = [
        "netconvert", "--osm-files", str(osm_file), "-o", str(net_file),
        *NETCONVERT_OPTIONS, *side, *region.netconvert_options, *types, *crop,
    ]
    print("  running netconvert ...")
    subprocess.run(cmd, check=True)


def build_geojson(net_file: Path, out: Path, extra_props: Callable[[object], dict] | None = None) -> dict:
    """Roads for the map. `extra_props(edge)` adds or overrides feature properties."""
    net = sumolib.net.readNet(str(net_file), withInternal=False)
    proj = NetProjection.from_net_file(net_file)
    features = []
    total_len = 0.0
    main_lane_len = 0.0
    for edge in net.getEdges():
        if edge.getFunction():  # internal / walkingarea / crossing
            continue
        lanes = edge.getLanes()
        # Lane shapes are offset from the road centre line, so both directions
        # of a two-way street stay visually separate.
        shape = lanes[len(lanes) // 2].getShape()
        lons, lats = proj.to_lonlat([p[0] for p in shape], [p[1] for p in shape])
        total_len += edge.getLength()
        if edge.getType() in MAIN_ROAD_TYPES:
            main_lane_len += edge.getLength() * len(lanes)
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": edge.getID(),
                    "name": edge.getName() or "",
                    "lanes": len(lanes),
                    "speed": round(edge.getSpeed(), 2),
                    "type": edge.getType(),
                    # Rickshaws are banned here unless "allowed on main roads" is on.
                    "main": not edge.allows("bicycle") and edge.allows("moped"),
                    "length": round(edge.getLength(), 1),
                    **(extra_props(edge) if extra_props else {}),
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [round(lo, 6), round(la, 6)] for lo, la in zip(lons, lats)
                    ],
                },
            }
        )
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return {
        "edges": len(features),
        "road_km": round(total_len / 1000, 1),
        "main_lane_km": round(main_lane_len / 1000, 1),
    }


def prepare_area(region: Region, spec: AreaSpec, force_download=False) -> Path:
    area_id, bbox = spec.id, spec.bbox
    area_dir = AREAS_DIR / area_id
    area_dir.mkdir(parents=True, exist_ok=True)
    osm_file = area_dir / "map.osm"
    net_file = area_dir / "net.net.xml"

    print(f"[{region.id}/{area_id}] {spec.name}")
    if force_download or not osm_file.exists():
        download_osm(bbox, osm_file, spec.detail)
    build_net(osm_file, net_file, bbox, region)
    added = add_automated_signals(net_file, region.signals)
    if added:
        print(f"  added {added} automatic signal{'s' if added > 1 else ''} OSM doesn't map")
    stats = build_geojson(net_file, area_dir / "network.geojson")
    # Kept apart from map.osm, which deployments leave out.
    from .features import _osm_bus_stops

    (area_dir / "bus_stops.json").write_text(json.dumps(_osm_bus_stops(area_dir), ensure_ascii=False))

    west, south, east, north = bbox
    # demand_scale and through_scale come from the region when the area loads
    # (region.area_info), so tuning the region needs no rebuild.
    meta = {
        "id": area_id,
        "name": spec.name,
        "region": region.id,
        "city": region.name,
        "kind": spec.kind,
        "detail": spec.detail,
        "bbox": list(bbox),
        "center": [(west + east) / 2, (south + north) / 2],
        **stats,
    }
    (area_dir / "area.json").write_text(json.dumps(meta, indent=2))
    # Layout variants were built from the old network (their ids hash only the edits).
    shutil.rmtree(VARIANTS_DIR / area_id, ignore_errors=True)
    print(f"  done: {stats['edges']} edges, {stats['road_km']} km of road")
    return area_dir


def _is_built(area_id: str) -> bool:
    return (AREAS_DIR / area_id / "area.json").exists() and (AREAS_DIR / area_id / "net.net.xml").exists()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("areas", nargs="*", metavar="area", help="area ids from the region packs (see traffic-sim-region list)")
    p.add_argument("--all", action="store_true", help="build every listed area")
    p.add_argument("--missing", action="store_true", help="build every listed area that isn't built yet")
    p.add_argument("--region", action="append", help="with --all/--missing: only this region (repeatable); "
                   "with --bbox: the region a custom area belongs to (default: the default region)")
    p.add_argument("--bbox", help="west,south,east,north: build an area that no region pack lists")
    p.add_argument("--name")
    p.add_argument("--download", action="store_true", help="re-download OSM even if cached")
    args = p.parse_args(argv)
    try:
        regions = all_regions()
    except RegionError as e:
        sys.exit(f"invalid region packs (run `traffic-sim-region check`):\n{e}")

    if args.bbox:
        if len(args.areas) != 1:
            p.error("--bbox builds one area: give exactly one id")
        bbox = tuple(float(v) for v in args.bbox.split(","))
        if len(bbox) != 4:
            p.error("--bbox needs 4 numbers")
        region = get_region(args.region[0]) if args.region else default_region()
        spec = AreaSpec(args.areas[0], args.name or args.areas[0], bbox)
        prepare_area(region, spec, force_download=args.download)
        return

    if args.all or args.missing:
        picked = [regions[r] for r in args.region or regions if r in regions]
        unknown = set(args.region or ()) - set(regions)
        if unknown:
            p.error(f"no region pack: {', '.join(sorted(unknown))}")
        todo = [(r, a) for r in picked for a in r.areas.values() if args.all or not _is_built(a.id)]
        if not todo:
            print("all areas built")
            return
    elif args.areas:
        todo, unknown = [], []
        for area_id in args.areas:
            found = find_area(area_id)
            if found:
                todo.append(found)
            else:
                unknown.append(area_id)
        if unknown:
            p.error(f"not in any region pack: {', '.join(unknown)} (add them to region.toml, or pass --bbox)")
    else:
        p.error("give area ids, --missing or --all")

    failed = []
    for region, spec in todo:
        try:
            prepare_area(region, spec, args.download)
        except Exception as e:  # noqa: BLE001 — keep going, report at the end
            print(f"  FAILED: {e}")
            failed.append(spec.id)
    if failed:
        sys.exit(f"failed: {', '.join(failed)}; re-run with those ids")


if __name__ == "__main__":
    sys.exit(main())
