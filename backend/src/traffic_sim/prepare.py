"""Build a simulation area from OpenStreetMap.

    uv run traffic-sim-prepare farmgate mirpur      # one or more presets
    uv run traffic-sim-prepare --all                # the whole city + every neighbourhood
    uv run traffic-sim-prepare pallabi --name "Pallabi" --bbox 90.355,23.815,90.375,23.830

Steps: download OSM (Overpass) → netconvert → signals on Dhaka's automatic
corridors (signals.py) → network.geojson for the map.
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

import sumolib
import sumo

from .config import AREA_PRESETS, AREAS_DIR
from .geo import NetProjection
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

# Types that count as "main roads" for demand scaling.
MAIN_ROAD_TYPES = {f"highway.{t}" for t in ("trunk", "primary", "secondary", "tertiary")}
# Main-road lane-km of the Farmgate area, which the presets' volumes were tuned
# on. Other areas scale the presets' volumes by their own main-road lane-km.
REFERENCE_MAIN_LANE_KM = 129.2

CITY_THROUGH_SCALE = 0.25

TYPE_FILES = [
    Path(sumo.SUMO_HOME) / "data" / "typemap" / "osmNetconvert.typ.xml",
    Path(__file__).with_name("dhaka.typ.xml"),  # rickshaw main-road rule
]

NETCONVERT_OPTIONS = [
    "--lefthand",  # Bangladesh drives on the left
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


def build_net(osm_file: Path, net_file: Path, bbox: tuple) -> None:
    # Overpass returns whole ways, which can run far outside the box; crop them.
    crop = ["--keep-edges.in-geo-boundary", ",".join(str(v) for v in bbox)]
    types = ["--type-files", ",".join(str(f) for f in TYPE_FILES)]
    cmd = [
        "netconvert", "--osm-files", str(osm_file), "-o", str(net_file),
        *NETCONVERT_OPTIONS, *types, *crop,
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


def prepare_area(
    area_id: str, name: str, city: str, bbox: tuple,
    kind: str = "area", detail: str = "full", force_download=False,
) -> Path:
    area_dir = AREAS_DIR / area_id
    area_dir.mkdir(parents=True, exist_ok=True)
    osm_file = area_dir / "map.osm"
    net_file = area_dir / "net.net.xml"

    print(f"[{area_id}] {name}")
    if force_download or not osm_file.exists():
        download_osm(bbox, osm_file, detail)
    build_net(osm_file, net_file, bbox)
    added = add_automated_signals(net_file)
    if added:
        print(f"  added {added} automatic signal{'s' if added > 1 else ''} OSM doesn't map")
    stats = build_geojson(net_file, area_dir / "network.geojson")
    # Kept apart from map.osm, which deployments leave out.
    from .features import _osm_bus_stops

    (area_dir / "bus_stops.json").write_text(json.dumps(_osm_bus_stops(area_dir), ensure_ascii=False))

    west, south, east, north = bbox
    meta = {
        "id": area_id,
        "name": name,
        "city": city,
        "kind": kind,
        "detail": detail,
        "bbox": list(bbox),
        "center": [(west + east) / 2, (south + north) / 2],
        **stats,
        "demand_scale": round(max(0.1, stats["main_lane_km"] / REFERENCE_MAIN_LANE_KM), 2),
        # Presets' through shares are for a neighbourhood, where most traffic is
        # passing through. Across the whole city most trips start and end inside.
        "through_scale": CITY_THROUGH_SCALE if kind == "city" else 1.0,
    }
    (area_dir / "area.json").write_text(json.dumps(meta, indent=2))
    print(f"  done: {stats['edges']} edges, {stats['road_km']} km of road")
    return area_dir


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("areas", nargs="*", metavar="area", help=f"area ids; presets: {', '.join(AREA_PRESETS)}")
    p.add_argument("--all", action="store_true", help="build every preset area")
    p.add_argument("--bbox", help="west,south,east,north (required for non-preset areas)")
    p.add_argument("--name")
    p.add_argument("--city", default="Dhaka")
    p.add_argument("--download", action="store_true", help="re-download OSM even if cached")
    args = p.parse_args(argv)

    if args.all or len(args.areas) > 1:
        ids = list(AREA_PRESETS) if args.all else args.areas
        unknown = [a for a in ids if a not in AREA_PRESETS]
        if unknown:
            p.error(f"not presets: {', '.join(unknown)} (custom areas are built one at a time)")
        failed = []
        for area_id in ids:
            try:
                _prepare_preset(area_id, args.download)
            except Exception as e:  # noqa: BLE001 — keep going, report at the end
                print(f"  FAILED: {e}")
                failed.append(area_id)
        if failed:
            sys.exit(f"failed: {', '.join(failed)}; re-run with those ids")
        return
    if not args.areas:
        p.error("give an area id or --all")

    area_id = args.areas[0]
    if args.bbox:
        bbox = tuple(float(v) for v in args.bbox.split(","))
        if len(bbox) != 4:
            p.error("--bbox needs 4 numbers")
        prepare_area(area_id, args.name or area_id, args.city, bbox, force_download=args.download)
    elif area_id in AREA_PRESETS:
        _prepare_preset(area_id, args.download)
    else:
        p.error(f"'{area_id}' is not a preset; pass --bbox")


def _prepare_preset(area_id: str, force_download: bool) -> None:
    preset = AREA_PRESETS[area_id]
    prepare_area(
        area_id, preset["name"], preset.get("city", "Dhaka"), preset["bbox"],
        kind=preset.get("kind", "area"), detail=preset.get("detail", "full"),
        force_download=force_download,
    )


if __name__ == "__main__":
    sys.exit(main())
