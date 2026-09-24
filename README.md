![Dhaka Traffic Sim](frontend/public/banner.png)

# Dhaka Traffic Sim

A web app for trying out traffic conditions on real Dhaka roads. Pick the whole city
or one of 15 neighbourhoods and a time of day, adjust how many vehicles enter and what
they are (cars, buses, CNGs, battery rickshaws, motorcycles, trucks, pedal rickshaws), and
watch congestion build up live on the map.

The simulation engine is [SUMO](https://eclipse.dev/sumo/) running on OpenStreetMap road
data. The browser only draws the map and controls; each browser tab gets its own SUMO
process on the server.

You can edit the roads while it runs: close roads or lanes, retime signals, add U-turns
and turn rules, and place bus stops, stands, crossings, market zones, rain and flooding.

**Status: early, uncalibrated.** Traffic volumes and driver behaviour are informed
guesses, so use it to compare scenarios, not to predict real travel times. Calibrating
against real vehicle counts is next (see [docs/model.md](docs/model.md)).

## Run it

Requires [uv](https://docs.astral.sh/uv/) and Node 20+. SUMO installs from PyPI; there is
nothing to install system-wide.

One command does everything below: it installs dependencies, builds any missing areas
and runs the backend and frontend together. Ctrl+C stops both.

```sh
./dev.sh                  # http://localhost:5173
./dev.sh --prepare-all    # also rebuild every area's network (--download: re-fetch OSM)
./dev.sh --no-prepare     # skip area building
```

Or step by step:

```sh
# 1. Backend: install, build the road networks (~1 min per area), start the server
cd backend
uv sync
uv run traffic-sim-prepare --missing  # whole city + 15 neighbourhoods (~15 min, mostly download)
uv run traffic-sim-server             # http://127.0.0.1:8000

# 2. Frontend (second terminal)
cd frontend
npm install
npm run dev                           # http://localhost:5173
```

To deploy it on a server with Docker, see [docs/deployment.md](docs/deployment.md).

### Add a neighbourhood or a city

Areas and cities are **region packs**: data files in `backend/src/traffic_sim/regions/`
(bounding boxes, presets, which signals run, flood zones). Adding a neighbourhood is one
table in `regions/dhaka/region.toml`; adding a city is a copy of `regions/_template/`.

```sh
cd backend
uv run traffic-sim-region check         # validate the packs
uv run traffic-sim-prepare <area>       # build a new area
uv run traffic-sim-run <area>           # headless run: the side panel's stats, no browser
```

See [docs/regions.md](docs/regions.md) for the format.
[docs/agent-guide.md](docs/agent-guide.md) has step-by-step playbooks for adding and tuning
locations, written for coding agents such as Claude Code and Codex.

## Documentation

| | |
| --- | --- |
| [How the simulation works](docs/model.md) | Editing roads, choices specific to Dhaka, reading the numbers |
| [Region packs](docs/regions.md) | Adding and tuning cities and neighbourhoods: the file format |
| [Agent guide](docs/agent-guide.md) | Playbooks for coding agents: add an area or city, tune demand and signals |
| [Deploying](docs/deployment.md) | Docker, sizing, reverse proxies |
| [Developer guide](docs/development.md) | Architecture, modules, common changes, testing |
| [Contributing](docs/CONTRIBUTING.md) | Ways to help, making a change, commit messages |

A plain-language guide for people using the simulator is built into the app: open
*How it works and how to use it* in the side panel, or go to `/#docs`.

## Tests

```sh
cd backend && uv run pytest     # runs real SUMO simulations over the WebSocket
cd frontend && npx tsc -b && npm run lint
```

Contributions are welcome, especially local knowledge and real traffic data. See
[docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).

## License

Copyright (c) 2026 SSafayet. Licensed under the [MIT License](LICENSE). Road data is
© OpenStreetMap contributors; see [NOTICE](NOTICE) for third-party data and software.
