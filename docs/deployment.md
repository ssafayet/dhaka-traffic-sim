# Deploying with Docker

The image holds everything: the SUMO binaries, the backend, the built frontend, the
region packs, and any road networks already built in `backend/data/areas`.

```sh
docker compose up -d --build           # http://<server>:8000
PORT=80 TRAFFIC_SIM_MAX_SIMS=8 docker compose up -d
```

- **Road networks.** Areas that a region pack lists but the image lacks are built on
  first start (`traffic-sim-prepare --missing`). This downloads from OpenStreetMap and
  takes ~15 min for all of Dhaka's on a fresh clone. They are kept in the `areas`
  volume. `PREPARE_AREAS=none` skips the build; `all` rebuilds every area.
- **Default city.** `TRAFFIC_SIM_REGION` picks the region the app opens on (default
  `dhaka`). Every region pack's built areas are listed either way.
- **Sizing.** Every open browser tab runs its own SUMO process, and SUMO is
  single-threaded. `TRAFFIC_SIM_MAX_SIMS` (default 4) caps how many run at once, so give
  the server about that many cores. Later tabs get "server busy".
- **Behind a reverse proxy** (nginx, Caddy, Traefik), forward WebSocket upgrades for
  `/ws/`. In nginx that means `proxy_http_version 1.1` plus the `Upgrade`/`Connection`
  headers. Caddy's `reverse_proxy` does it automatically.
- **Different CPU.** To build for a server with a different CPU than your machine, use
  `docker buildx build --platform linux/amd64 -t traffic-sim .`. SUMO ships wheels for
  amd64 and arm64.

For a single-server setup without Docker, run `npm run build` in `frontend/`.
`traffic-sim-server` then serves the built app at http://127.0.0.1:8000.
