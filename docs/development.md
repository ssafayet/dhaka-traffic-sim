# Developer guide

How the simulator is put together and where to make common changes. For running it,
see the [README](../README.md); for deploying it, [deployment.md](deployment.md). For how
to submit changes, see [CONTRIBUTING.md](CONTRIBUTING.md). Adding or tuning a city or
neighbourhood is a data change: see [regions.md](regions.md), and
[agent-guide.md](agent-guide.md) for step-by-step playbooks.

## Setup

You need [uv](https://docs.astral.sh/uv/) and Node 20+. SUMO comes from PyPI
(`eclipse-sumo`), so there is nothing to install system-wide.

```sh
./dev.sh                  # installs deps, builds missing areas, runs both servers
```

`dev.sh` runs the backend with `--reload`, so Python changes restart the server, and
the Vite dev server hot-reloads the frontend. Vite proxies `/api` and `/ws` to the
backend (`frontend/vite.config.ts`). Set `BACKEND_PORT` / `FRONTEND_PORT` if the
defaults (8000 / 5173) are taken.

The first run downloads OpenStreetMap data for every area (~15 min). While you work on
one area, build only that one and skip the rest:

```sh
cd backend && uv run traffic-sim-prepare farmgate
./dev.sh --no-prepare
```

## Architecture

```
Browser (React + MapLibre + deck.gl)
   │  REST  /api/...        regions + presets, areas, road network GeoJSON, vehicle types, layouts
   │  WebSocket /ws/sim     start / live edits  →   ← frames, status, events
   ▼
FastAPI server (server.py)
   │  one thread + one SUMO process per WebSocket connection
   ▼
SUMO over TraCI (engine.py)
```

The browser only draws. All the simulation runs on the server: each WebSocket
connection gets its own `Simulation` (`engine.py`) running SUMO in a background thread
(`SimRunner` in `server.py`). The thread steps SUMO, builds frames and puts them on an
asyncio queue that the WebSocket handler sends out. When the queue is full, older
frames are dropped rather than let the browser fall behind. `TRAFFIC_SIM_MAX_SIMS`
caps how many simulations run at once.

Frames carry vehicle positions as parallel arrays (`ids`, `lon`, `lat`, `angle`,
`speed`, `kind`) to keep them small. The server sends fewer frames per second as the
vehicle count grows, and `frontend/src/lib/vehicleStore.ts` interpolates between
frames so vehicles still move at 60 fps.

### Region packs

Everything specific to a city is data in a region pack,
`backend/src/traffic_sim/regions/<id>/`: its areas (bounding boxes), driving side,
road type overrides, time-of-day presets, where signals run automatically, and default
flood zones. `region.py` loads and validates the packs, and the rest of the code asks it
rather than holding city constants. The format is in [regions.md](regions.md).

```
region.toml, presets.toml, signals.toml, waterlogging.json, *.typ.xml
   │  region.py: parse + validate (traffic-sim-region check)
   ▼
Region ──► prepare.py   driving side, type files, bbox, signals to add
       ──► engine.Area  area_info(): demand_scale, through_scale from the reference area
       ──► scenario.py  which signals are "automated" (signals.is_automated)
       ──► features.py  default flood zones
       ──► server.py    /api/regions: presets, defaults, signal notes
```

Vehicle types (`vtypes.py`) are shared by every region; a region's presets only choose
the mix.

### Data flow for an area

1. `traffic-sim-prepare` (`prepare.py`) looks the area up in the region packs, downloads
   OSM for its bounding box from Overpass, runs `netconvert` with the region's driving
   side and road types, and writes into `backend/data/areas/<id>/`:
   - `net.net.xml`: the SUMO network
   - `network.geojson`: the roads as the map draws them
   - `area.json`: name, region, bbox, `main_lane_km` and other metadata
   Preparing also signals the junctions the region's `signals.toml` lists that OSM leaves
   unsignalled (`signals.py`). Where the region says `elsewhere = "off"` (Dhaka), other
   signals start switched off (police-directed); topology marks each signal
   `automated`, and `Simulation.set_signal(tls, None)` restores that default.
2. `load_area()` (`engine.py`) reads that directory, adds the values its region decides
   (`region.area_info`: `demand_scale`, `through_scale`), and caches the parsed network,
   the routable edge pools per vehicle class (`demand.py`) and the topology used by the
   road editor (`scenario.py`). Because the scaling is computed here, tuning a region
   needs no rebuild.
3. On `start`, `Simulation` launches SUMO on the network. `demand.py` generates trips
   live, not from a pre-built route file: through traffic enters and leaves at the area
   border, and local trips start and end on streets weighted by lane-km.

Nothing in `backend/data/` is committed; it is all rebuilt from OSM.

### Two kinds of road edit

- **Live edits** (closures, signal timings, road features, weather) are sent over the
  WebSocket and applied to the running simulation through TraCI. They do not restart
  the run.
- **Layout edits** (U-turns, turn rules, added signals) change the network itself.
  The frontend POSTs them to `/api/areas/{id}/variants`. `scenario.py` validates them,
  rebuilds the network with `netconvert` into `backend/data/variants/`, and returns a
  variant id, which the frontend passes to `start`. Variants are keyed by a hash of the
  canonical edits, so identical edits reuse the same network. Edits are always relative
  to the unedited area.

## Backend modules

`backend/src/traffic_sim/`

| Module | What it does |
| --- | --- |
| `config.py` | Paths and the default region (`TRAFFIC_SIM_REGION`) |
| `regions/<id>/` | Region packs: areas, presets, signals, flood zones, road types (data) |
| `region.py` | Loads and validates region packs; demand scaling; `traffic-sim-region` CLI |
| `prepare.py` | OSM download → netconvert → `network.geojson` and `area.json` |
| `signals.py` | Applies a region's signal policy; adds the missing signals when an area is prepared |
| `vtypes.py` | Vehicle types (shared): size, speed, gap-taking; writes the SUMO vTypes |
| `demand.py` | Live trip generation and routable edge pools |
| `run.py` | `traffic-sim-run`: a headless run that prints stats, for tuning |
| `engine.py` | `Area`, `Simulation`: one SUMO run over TraCI, stats, frames |
| `scenario.py` | Closures, signal plans, layout edits and variant building |
| `features.py` | Bus stops, stands, crossings, hot zones, waterlogging, weather |
| `geo.py` | Coordinate conversion helpers |
| `server.py` | FastAPI app: REST endpoints and the `/ws/sim` WebSocket |

The WebSocket protocol, every message in both directions, is documented in the
docstring at the top of `server.py`. Keep it up to date when you change a message.

User input is validated at the edge. `scenario.py` and `features.py` parse and clamp
everything coming from the browser. Invalid layout edits raise `EditError` (HTTP 422
with per-edit messages). An invalid live edit sends a `warning` and the run goes on.

## Frontend

`frontend/src/`

| Path | What it does |
| --- | --- |
| `App.tsx` | Top-level state: area, demand, edits, simulation lifecycle |
| `lib/simClient.ts` | WebSocket client; sends `start` and live edits, parses messages |
| `lib/api.ts` | REST calls |
| `lib/types.ts` | Types shared with the backend protocol |
| `lib/vehicleStore.ts` | Frame buffer and interpolation |
| `lib/scenario.ts`, `lib/features.ts` | Edit state and conversion to server messages |
| `components/MapView.tsx` | MapLibre base map plus deck.gl roads and vehicles |
| `components/Inspector.tsx` | Road editor for the selected road, junction or signal |
| `components/MapTools.tsx`, `FeatureEditor.tsx` | Placing and editing road features |
| `components/ControlPanel.tsx`, `StatsPanel.tsx` | Side panel controls and live stats |
| `components/Docs.tsx` | The in-app guide at `/#docs` |

There is no state library. State lives in `App.tsx` and is passed down. Vehicle
positions bypass React: `vehicleStore` is read on every animation frame by the deck.gl
layer, so 60 fps rendering doesn't trigger React re-renders.

## Common changes

### Add an area, a city, or tune one

All data; see [agent-guide.md](agent-guide.md) for the steps and [regions.md](regions.md)
for the fields. In short: add `[areas.<id>]` to the region's `region.toml`, run
`uv run traffic-sim-region check`, build with `uv run traffic-sim-prepare <id>`, and
compare with `uv run traffic-sim-run <id>`. Presets live in the region's
`presets.toml`; `volume` is vehicles per hour *for the region's reference area* (Farmgate,
for Dhaka), and other areas scale it automatically.

### Change what a region pack can express

Add the field to the dataclasses and parser in `region.py`, use it where it applies,
document it in [regions.md](regions.md), add it (commented) to `regions/_template/`, and
cover it in `tests/test_regions.py`. The test that the template parses keeps the
template in step.

### Add a vehicle type

1. Add a `VehicleType` to `VEHICLE_TYPES` in `vtypes.py`. Every region shares it.
2. Add it to every preset's `mix` in each region's `presets.toml` (0 where it doesn't
   run; `traffic-sim-region check` warns about presets that leave it out).
3. Add its colour to both palettes in `frontend/src/lib/palette.ts`.

The frontend gets the list, sizes and labels from `/api/vehicle-types`, and
`vehicleIcons.ts` draws a to-scale silhouette from its length and width.

### Add a live edit or protocol message

1. Handle it in `SimRunner._handle` (`server.py`), and in `Simulation` if it touches
   SUMO.
2. Document it in the `server.py` docstring.
3. Add the type in `frontend/src/lib/types.ts` / `simClient.ts`.
4. Add a WebSocket test in `backend/tests/test_server.py`.

### Add a road feature

Parse and validate it in `features.py` (`parse_features`), apply it in
`Simulation.set_features` (`engine.py`), add its editing UI in `FeatureEditor.tsx` /
`MapTools.tsx`, and convert it in `frontend/src/lib/features.ts`.

## Testing

```sh
cd backend && uv run pytest              # most tests need the farmgate area built
cd frontend && npx tsc -b && npm run lint
```

- `test_regions.py`: region pack parsing, validation, signal policies and demand
  scaling. Runs without SUMO or built areas.
- `test_scenario.py`: closure, signal and layout edit validation, variant building.
- `test_features.py`: road feature parsing and placement.
- `test_server.py`: REST endpoints and full simulations over the WebSocket.

The other tests use the real Farmgate network and real SUMO, and are skipped if
`backend/data/areas/farmgate/` hasn't been built. There are no frontend unit tests;
check UI changes by hand in the browser, in light and dark mode.

## Debugging tips

- **See what SUMO sees.** `sumo-gui` is installed with `eclipse-sumo`. Open an area's
  network with `uv run sumo-gui -n data/areas/<id>/net.net.xml` to inspect lanes,
  junctions and connections.
- **Watch the WebSocket.** Frames and events are visible in the browser devtools
  Network tab (filter by WS).
- **Rebuild an area from scratch** if its network looks wrong after changing
  `prepare.py` or a region's `.typ.xml`: `uv run traffic-sim-prepare <id>`, or add
  `--download` to re-fetch OSM. Rebuilding deletes the area's layout variants.
- **Try a change without the browser:** `uv run traffic-sim-run <area>` prints the
  stats the side panel shows.
- **Stale layout variants** live in `backend/data/variants/` and are safe to delete.
