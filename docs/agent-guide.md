# Agent guide: adding and tuning locations

For coding agents (Claude Code, Codex, and others) asked to add a city or neighbourhood,
or to tune how one behaves. Every city lives in a **region pack**: data files in
`backend/src/traffic_sim/regions/<region>/`. [regions.md](regions.md) is the field
reference; this guide is the playbooks. Run every command from `backend/`.

You can't watch the map, so your eyes are three commands:

| Command | Tells you |
| --- | --- |
| `uv run traffic-sim-region check [<region>]` | Pack errors and warnings, and the calibration value to copy after a build. Exit 1 on errors |
| `uv run traffic-sim-region list` | Every area: size in km, built or not |
| `uv run traffic-sim-run <area> [--preset id] [--minutes 30] [--json]` | A headless run: the side panel's stats every 5 simulated minutes, then a summary. `--help` for overrides |

A pack edit is done when `check` is clean and a headless run of each touched area
finishes. Where a playbook says **report**, tell the human what you changed, the before
and after numbers, and every number you guessed.

## Playbook: add a neighbourhood to an existing region

1. **Find the bbox.** Ask the human, or look it up on Nominatim:
   `curl -s -A traffic-sim 'https://nominatim.openstreetmap.org/search?q=<place>&format=json&limit=1'`.
   Nominatim's `boundingbox` is `[south, north, west, east]`; region packs want
   `[west, south, east, north]`. Keep it under 3 × 3 km. Widen a tight suburb box to
   take in the main roads that feed it.
2. **Add it** as `[areas.<id>]` in the region's `region.toml`. The id must be unused in
   every region (`traffic-sim-region list` shows them all).
3. **Check:** `uv run traffic-sim-region check <region>`. Done when it prints `ok` and no
   warning names your area.
4. **Build:** `uv run traffic-sim-prepare <id>`. It takes about a minute, mostly the
   OpenStreetMap download. Done when it prints `done: N edges`. Under ~200 edges means the
   bbox missed the town; over ~8,000 will simulate slowly.
5. **Run:** `uv run traffic-sim-run <id> --minutes 20`. Done when it finishes with
   `stuck_removed` under about 2% of `final_running`. A higher share usually means a
   broken network, so check the bbox before touching demand.
6. **Report** the edge count, road km and the run summary.

The presets scale to the new area by its main-road lane-km (regions.md, *How demand
scales*). It needs no demand tuning until someone compares it with real counts.

## Playbook: add a new region (city)

1. **Copy the template:** `cp -r src/traffic_sim/regions/_template src/traffic_sim/regions/<region>`.
   The folder name is the region id.
2. **Fill `region.toml`:** name, country, `driving_side`, one or more areas (first
   playbook, step 1), and `default_area`/`reference_area` pointing at your most central
   neighbourhood. Leave `reference_main_lane_km` for step 5. Delete `waterlogging.json`
   unless you have sourced flood spots (see *Add flood zones* below).
3. **Fill `presets.toml`:** a vehicle mix for the city from a survey or a published
   modal share, cited in a comment. Mix ids are the shared vehicle types in `vtypes.py`.
   Give absent types weight 0. For volumes, start from the template's and tune them in
   step 7.
4. **Fill `signals.toml`:** `elsewhere = "on"` for a city whose signals work. Use
   `elsewhere = "off"` only where police direct most junctions, and then list the
   automatic places. No signal data means leave it `"on"`.
5. **Build and calibrate:** `uv run traffic-sim-prepare --missing --region <region>`,
   then `uv run traffic-sim-region check <region>`. The check's warning gives the value
   for `demand.reference_main_lane_km`. Copy it in, and run the check again until it
   prints `ok` with no calibration warning.
6. **Run the full test suite:** `uv run pytest`. It includes a test that every shipped
   pack parses.
7. **Tune the presets** with the next playbook until the pattern looks right.
8. **Report** the sources for the mix, every guessed number, and a table of the
   presets' run summaries.

If the city needs a vehicle that `vtypes.py` lacks (a jeepney, a tuk-tuk), that is a
code change shared by every region. Stop and ask the human. The steps are in
[development.md](development.md#add-a-vehicle-type).

## Playbook: tune demand

The goal is a plausible *pattern* (free-ish midday, heavy rush hour, light night), or
a match to real counts when the human has them. Tune presets on the reference area only;
the other areas follow by scaling.

1. **Measure the baseline.** Run every preset on the reference area with the same seed,
   and keep the summaries:
   `uv run traffic-sim-run <ref> --preset <id> --minutes 30 --json | tail -1`
2. **Adjust one knob at a time**, using the overrides so you don't edit files between trials:
   - `--volume N`: vehicles per hour. The biggest lever on delay.
   - `--through X`: the share of trips crossing the area. Higher loads the main roads and the border.
   - the `mix` weights, edited in `presets.toml`. More buses and trucks slow a road
     more than cars do; motorcycles and rickshaws filter through gaps.
3. **Read the result** against the target (table below). Prefer a volume at which
   delay is still rising steadily over one where the network has locked up, since a
   locked network hides further changes.
4. **Write the value back.** The run's setup line prints `demand_scale`, and on the
   reference area it is 1, so the `--volume` you settled on is the preset's `volume`.
   Put the source or "guess, tuned to <target>" in the comment above it.
5. **Confirm on a second seed** (`--seed 7`). Done when both seeds land in the same
   band of the table.
6. **Report** a before and after table per preset.

| Stat | Free flow | Heavy | Gridlock |
| --- | --- | --- | --- |
| `delay` (min lost per vehicle on the road) | < 1 | 2–6 | > 8 and climbing every row |
| `km/h` (average speed) | > 25 | 10–20 | < 8 |
| `waiting` (vehicles that can't get onto the network) | 0 | tens | hundreds and growing |
| `stuck` (removed after 5 min stuck) | ~0 | a few | many |

`trip` (average trip time) only counts finished trips, so it can look fine during
gridlock. Trust `delay`.

## Playbook: tune signals

1. Find signal positions and ids: `curl -s localhost:8000/api/areas/<area>/topology`
   (with the server running) lists `signals` with `lon`, `lat` and `automated`. Or
   load the area in Python (`traffic_sim.engine.load_area("<area>").signals`).
2. Edit `signals.toml`: see regions.md for `elsewhere`, `junctions`, `corridors`
   and `zones`.
3. Changes to `junctions`, or to a zone with `add_missing`, add signals at build time.
   Rebuild with `uv run traffic-sim-prepare <area>`. Every other change needs only a
   server restart.
4. **Check:** the topology's `automated` flags match what you intended, and a headless
   run finishes. Done when both hold for every affected area.

## Add flood zones

`waterlogging.json` holds only places reported flooded by a named source (news report,
city agency), and every spot lists its sources. Positions at neighbourhood or
intersection level are fine. The depth is the worst reported: `shallow` (ankle), `knee`
or `deep` (waist). Check with `traffic-sim-run <area> --rain heavy`, where the delay
should rise over the dry run.

## Rules for every change

- **Cite or flag every real-world number.** A volume, share, speed or position gets a
  comment naming its source, or saying it is a guess and what it was tuned to. The Dhaka
  pack shows the style.
- **One region per change.** Tuning a new city leaves `regions/dhaka/` as it is. The
  Dhaka numbers are calibrated against each other.
- **Compare with the same seed.** A/B runs differ only in the knob under test. The
  simulator is for comparisons, not forecasts.
- **The server caches areas.** After a build, restart it (`./dev.sh` restarts itself
  on pack edits but not on builds).
- **Overpass is slow and rate-limited.** A failed download retries the next mirror on
  its own. Re-run a failed id alone. `data/areas/<id>/map.osm` is cached, and `--download`
  forces a fresh copy.
- **Keep the UI in step.** User-visible behaviour belongs in the README and, where it
  applies, the in-app guide (`frontend/src/components/Docs.tsx`, which describes Dhaka).

## Where things are

| Need | Look at |
| --- | --- |
| Pack loading and validation, demand scaling | `backend/src/traffic_sim/region.py` |
| How signal policies apply | `backend/src/traffic_sim/signals.py` |
| OSM download and netconvert options | `backend/src/traffic_sim/prepare.py` |
| Vehicle types (shared) | `backend/src/traffic_sim/vtypes.py` |
| Headless runs | `backend/src/traffic_sim/run.py` |
| Pack tests | `backend/tests/test_regions.py` |
| Architecture | [development.md](development.md) |
