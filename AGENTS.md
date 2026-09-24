# AGENTS.md

A SUMO traffic simulator on OpenStreetMap roads. A FastAPI backend (`backend/`, Python
with uv) runs one SUMO process per browser tab and streams it to a React frontend
(`frontend/`). It ships with Dhaka.

## Locations are data

Cities and neighbourhoods are **region packs**: TOML and JSON files in
`backend/src/traffic_sim/regions/<id>/`. To add a neighbourhood or a city, or to tune
demand, signals or flood zones, follow the playbooks in
[docs/agent-guide.md](docs/agent-guide.md). The file format is in
[docs/regions.md](docs/regions.md).

## Before you finish

```sh
cd backend && uv run pytest                  # most tests skip until farmgate is built: uv run traffic-sim-prepare farmgate
cd backend && uv run traffic-sim-region check  # after any region pack edit
cd frontend && npx tsc -b && npm run lint
```

## Conventions

- Every constant that stands for something real (a speed, share, volume, position)
  carries a comment naming its source, or saying it is a guess.
- The WebSocket protocol is documented in the `server.py` docstring. Change it there
  when you change a message.
- Commits follow Conventional Commits: `feat(backend): …`, `fix(frontend): …`, `docs: …`.
- Architecture and where to make common changes: [docs/development.md](docs/development.md).
