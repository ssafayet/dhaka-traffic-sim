# Region packs

Everything specific to one city lives in a **region pack**: a folder of data files
under `backend/src/traffic_sim/regions/<id>/`. The code reads cities only through
`region.py`, so adding a city, adding a neighbourhood or retuning one is a data change.

```
regions/
  _template/           copy this to start a new region (folders starting with "_" aren't loaded)
  dhaka/
    region.toml        required: name, driving side, defaults, demand reference, areas
    presets.toml       required: time-of-day scenarios
    signals.toml       optional: where signals run automatically (default: everywhere)
    waterlogging.json  optional: default flood zones, with sources
    roads.typ.xml      optional: netconvert road types, listed in region.toml
```

The folder name is the region id (lower-case letters, digits, `_`, `-`). After any edit, run:

```sh
cd backend
uv run traffic-sim-region check [<id>]   # errors, warnings, calibration hints; exit 1 on errors
uv run traffic-sim-region list           # regions, areas, sizes, which are built
```

The server refuses to start if a pack is invalid, and lists every problem.

## When a change takes effect

| You changed | Then |
| --- | --- |
| an area's `bbox`, `kind` or `detail`; a new area | `uv run traffic-sim-prepare <area>`, then restart the server |
| `driving_side`, `[network]`, a `.typ.xml` file | rebuild every area of the region: `uv run traffic-sim-prepare --all --region <id>` |
| `signals.toml` `junctions`, or a zone with `add_missing` | rebuild the affected areas (the missing signals are added at build time) |
| anything else: presets, `[demand]`, `elsewhere`, corridors, notes, `waterlogging.json`, names | restart the server (`./dev.sh` reloads on its own) |

`demand_scale` and `through_scale` are computed when an area loads, not when it is
built, so demand tuning never needs a rebuild. Rebuilding an area deletes its layout
variants (`data/variants/<area>/`), which were built from the old network.

## region.toml

| Key | Type | Meaning |
| --- | --- | --- |
| `name` | string | City name shown in the UI |
| `country` | string | Optional |
| `driving_side` | `"left"` \| `"right"` | Adds `netconvert --lefthand` for `"left"`; parked vehicles keep to that kerb |
| `default_area` | area id | The area the app opens on (for the default region) |
| `default_preset` | preset id | The preset selected on load and on switching to this region |
| `[demand].reference_area` | area id | The area the preset volumes are tuned on |
| `[demand].reference_main_lane_km` | number | That area's `main_lane_km` from `data/areas/<id>/area.json`. `check` prints the right value once the area is built |
| `[demand].city_through_scale` | 0..1 | Multiplies presets' `through_share` on `kind = "city"` areas. Default 1 |
| `[network].type_files` | list of file names | SUMO type files in the pack folder, loaded after SUMO's `osmNetconvert.typ.xml`. Types listed there replace SUMO's |
| `[network].netconvert_options` | list of strings | Extra netconvert options, after `prepare.NETCONVERT_OPTIONS` |
| `[areas.<id>]` | table | One per area; see below |

Area ids are global: two regions can't both have an area `centre`. Built areas go
to `backend/data/areas/<id>/`.

| Area key | Type | Meaning |
| --- | --- | --- |
| `name` | string | Shown in the Area dropdown |
| `bbox` | `[west, south, east, north]` | Degrees, **longitude first**. `check` catches reversed corners |
| `kind` | `"area"` (default) \| `"city"` | `"area"`: a neighbourhood, every street, about 3 × 3 km (warning over 4.5 km a side). `"city"`: main roads only, any size |
| `detail` | `"full"` \| `"arterial"` | Which OSM road classes to keep (`prepare.DETAIL_FILTERS`). Default: `full` for an area, `arterial` for a city |

### How demand scales

A preset's `volume` is vehicles per hour **on the reference area**. For any other area:

```
demand_scale  = area.main_lane_km / reference_main_lane_km     (min 0.1)
volume        = preset.volume × demand_scale
through_share = preset.through_share × (city_through_scale if kind == "city" else 1)
```

`main_lane_km` is the lane-km of trunk, primary, secondary and tertiary roads, measured
when the area is built. The UI's volume slider and the server's volume cap scale the
same way.

## presets.toml

A list of `[[presets]]`:

| Key | Type | Meaning |
| --- | --- | --- |
| `id` | string | Unique in the region |
| `label`, `description` | string | Shown on the preset chips |
| `start_hour` | 0..23.99 | The clock the run starts at |
| `volume` | number | Vehicles per hour entering the reference area |
| `through_share` | 0..1 | Share of trips that enter and leave at the area border; the rest start or end on a street inside |
| `mix` | table | Vehicle type id → relative weight. Ids are `VEHICLE_TYPES` in `vtypes.py` (`car`, `bus`, `cng`, `e_rickshaw`, `motorcycle`, `truck`, `rickshaw`). A missing id means weight 0 (and a warning) |

Vehicle types, their sizes and driving behaviour are shared by every region. A region
tunes how many of each there are, not how they drive.

## signals.toml

Decides which mapped signals run automatically by default. Users can switch any signal
on or off in the UI.

| Key | Type | Meaning |
| --- | --- | --- |
| `elsewhere` | `"on"` \| `"off"` | Signals outside the places below. `"on"`: every signal runs (most cities). `"off"`: they start switched off, which is how police-directed junctions are approximated (Dhaka) |
| `note_on`, `note_off` | string | The explanation the signal editor shows |
| `junctions` | list of `{name, lon, lat, radius}` | Always automatic. At build time a signal is added if OSM has none (roundabouts excepted). `radius` in metres must cover every node of the crossing |
| `[[corridors]]` | `{name, buffer, points}` | Signals within `buffer` metres of the polyline are automatic. Adds nothing |
| `[[zones]]` | `{name, polygon, add_missing}` | Signals inside the polygon are automatic. `add_missing = true` also signals every unsignalled main-road crossing inside at build time |

Points are `[lon, lat]`. Junction and zone names become signal ids (`auto_<name>`), so
they are unique and use letters, digits and `_`. Without the file, `elsewhere = "on"`.

## waterlogging.json

Default flood zones, used when the UI's weather floods the roads. Shape:

```json
{
  "about": "What these are and where they come from (shown in the UI).",
  "sources": { "KEY": { "outlet": "...", "date": "YYYY-MM-DD", "title": "...", "url": "..." } },
  "spots": [
    { "id": "w1", "name": "Place", "lon": 0, "lat": 0, "radius": 200, "depth": "shallow|knee|deep", "sources": ["KEY"] }
  ]
}
```

Every spot needs at least one source. An area gets the spots within about 300 m of its
bbox. Without the file, areas start with no flood zones.

## Road type files

A `.typ.xml` listed in `[network].type_files` overrides SUMO's defaults per OSM road
class: lanes, speed, which vehicle classes may use it. Dhaka's `roads.typ.xml` bans
`bicycle` from primary roads, which is how the "rickshaws on main roads" toggle works
(`vtypes.py` switches rickshaws between the `moped` and `bicycle` classes). See SUMO's
[type file docs](https://sumo.dlr.de/docs/SUMO_edge_type_file.html).
