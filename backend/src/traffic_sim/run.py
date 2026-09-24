"""Run a simulation headless and print its stats: the loop for tuning a region.

    uv run traffic-sim-run farmgate                          # default preset, 30 min
    uv run traffic-sim-run farmgate --preset evening_rush --minutes 60
    uv run traffic-sim-run mirpur --volume 7000 --through 0.5 --json

It starts like the browser does: the preset scaled to the area, the area's
default bus stops and waterlogging zones (dry unless --rain heavy), sublane
movement off for a whole-city area. Stats are the ones the side panel shows.
The first --warmup minutes fill the empty roads and are left out of the summary.
"""

import argparse
import json
import sys
import time

from .demand import DemandSettings
from .engine import SimOptions, Simulation, load_area
from .features import defaults as feature_defaults
from .features import parse_features
from .region import RegionError, preset_demand

COLUMNS = [
    ("clock", "clock"),
    ("running", "running"),
    ("waiting", "waiting"),
    ("avg_speed_kmh", "km/h"),
    ("on_road_delay_min", "delay"),
    ("avg_trip_min", "trip"),
    ("throughput_per_hour", "trips/h"),
    ("teleports", "stuck"),
]


def _clock(seconds: float) -> str:
    m = int(seconds // 60) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def _cell(key: str, v) -> str:
    if key == "clock":
        return _clock(v)
    return "-" if v is None else str(v)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("area")
    p.add_argument("--preset", help="preset id (default: the region's default preset)")
    p.add_argument("--minutes", type=float, default=30, help="simulated minutes after warm-up (default 30)")
    p.add_argument("--warmup", type=float, default=5, help="simulated minutes to fill the roads first (default 5)")
    p.add_argument("--every", type=float, default=5, help="simulated minutes between rows (default 5)")
    p.add_argument("--volume", type=float, help="vehicles per hour for this area (overrides the preset)")
    p.add_argument("--through", type=float, help="through-traffic share 0..1 (overrides the preset)")
    p.add_argument("--sublane", choices=("on", "off"), help="lane-free movement (default: off for a city area)")
    p.add_argument("--rickshaws", choices=("allowed", "banned"), default="allowed", help="rickshaws on main roads")
    p.add_argument("--rain", choices=("none", "light", "heavy"), default="none")
    p.add_argument("--no-features", action="store_true", help="leave out the default bus stops and water zones")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--json", action="store_true", help="one JSON object per row, then a summary object")
    args = p.parse_args(argv)

    try:
        area = load_area(args.area)
    except KeyError:
        sys.exit(f"'{args.area}' isn't built; run `uv run traffic-sim-prepare {args.area}`")
    except RegionError as e:
        sys.exit(f"invalid region packs (run `traffic-sim-region check`):\n{e}")
    region = area.region
    preset = region.preset(args.preset or region.default_preset)
    if preset is None:
        sys.exit(f"no preset '{args.preset}' in {region.id}: {', '.join(x.id for x in region.presets)}")

    d = preset_demand(preset, area.meta)
    if args.volume is not None:
        d["volume"] = args.volume
    if args.through is not None:
        d["through_share"] = args.through
    demand = DemandSettings(volume=d["volume"], mix=d["mix"], through_share=d["through_share"])
    sublane = args.sublane == "on" if args.sublane else area.meta.get("kind") != "city"
    options = SimOptions(
        sublane=sublane, rickshaws_on_main_roads=args.rickshaws == "allowed",
        start_hour=preset.start_hour, seed=args.seed,
    )
    raw = {"weather": {"rain": args.rain}}
    if not args.no_features:
        f = feature_defaults(area, area.dir)
        raw["bus_stops"] = [{"id": s["id"], "lon": s["lon"], "lat": s["lat"], "dwell": 30} for s in f["bus_stops"]]
        raw["water"] = [{k: w[k] for k in ("id", "lon", "lat", "radius", "depth")} for w in f["water"]]
    features = parse_features(area, raw)

    setup = {
        "area": area.id, "region": region.id, "preset": preset.id,
        # preset volume = this volume / demand_scale (see docs/regions.md)
        "demand_scale": area.meta.get("demand_scale", 1), "volume": demand.volume,
        "through_share": demand.through_share, "sublane": sublane, "rain": args.rain,
        "bus_stops": len(features.bus_stops), "water_zones": len(features.water),
    }
    if args.json:
        print(json.dumps({"setup": setup}))
    else:
        print(" ".join(f"{k}={v}" for k, v in setup.items()))
        print("  ".join(f"{label:>8}" for _, label in COLUMNS))

    warmup_steps = round(args.warmup * 60)
    total_steps = warmup_steps + round(args.minutes * 60)
    every = max(1, round(args.every * 60))
    rows = []
    sim = Simulation(area, demand, options, features=features)
    started = time.monotonic()
    try:
        sim.start()
        for i in range(1, total_steps + 1):
            want = i % every == 0 or i == total_steps
            frame = sim.step(want_frame=want)
            if frame is None:
                continue
            stats = {k: frame["stats"][k] for k, _ in COLUMNS if k in frame["stats"]}
            stats["warming_up"] = i <= warmup_steps
            rows.append(stats)
            if args.json:
                print(json.dumps(stats), flush=True)
            else:
                mark = " (warm-up)" if stats["warming_up"] else ""
                print("  ".join(f"{_cell(k, stats.get(k)):>8}" for k, _ in COLUMNS) + mark, flush=True)
    finally:
        sim.close()

    measured = [r for r in rows if not r["warming_up"]] or rows
    last = measured[-1]
    summary = {
        "mean_speed_kmh": round(sum(r["avg_speed_kmh"] for r in measured) / len(measured), 1),
        "mean_on_road_delay_min": round(sum(r["on_road_delay_min"] for r in measured) / len(measured), 2),
        "final_running": last["running"],
        "final_waiting": last["waiting"],
        "final_throughput_per_hour": last["throughput_per_hour"],
        "stuck_removed": last["teleports"],
        "wall_seconds": round(time.monotonic() - started, 1),
    }
    if args.json:
        print(json.dumps({"summary": summary}))
    else:
        print("summary: " + " ".join(f"{k}={v}" for k, v in summary.items()))


if __name__ == "__main__":
    main()
