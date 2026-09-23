#!/usr/bin/env bash
# Build and run the project for development.
#
#   ./dev.sh                 install deps, build missing areas, run backend + frontend
#   ./dev.sh --prepare-all   also rebuild every area's road network (uses cached OSM)
#   ./dev.sh --download      rebuild every area and re-download OSM data
#   ./dev.sh --no-prepare    skip building areas
#
# Backend: http://127.0.0.1:${BACKEND_PORT:-8000}   Frontend: http://localhost:${FRONTEND_PORT:-5173}
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

prepare="missing"
for arg in "$@"; do
  case "$arg" in
    --prepare-all) prepare="all" ;;
    --download) prepare="download" ;;
    --no-prepare) prepare="none" ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

for tool in uv node npm; do
  command -v "$tool" >/dev/null || { echo "missing '$tool'; see README.md" >&2; exit 1; }
done

step "Backend dependencies"
(cd "$ROOT/backend" && uv sync)

step "Frontend dependencies"
if [[ ! -d "$ROOT/frontend/node_modules" || "$ROOT/frontend/package-lock.json" -nt "$ROOT/frontend/node_modules/.package-lock.json" ]]; then
  (cd "$ROOT/frontend" && npm install)
else
  echo "up to date"
fi

step "Simulation areas"
case "$prepare" in
  none) echo "skipped" ;;
  all) (cd "$ROOT/backend" && uv run traffic-sim-prepare --all) ;;
  download) (cd "$ROOT/backend" && uv run traffic-sim-prepare --all --download) ;;
  missing)
    # Areas without a built network; the first run downloads OSM (~15 min for all).
    missing=$(cd "$ROOT/backend" && uv run python -c "
from traffic_sim.config import AREA_PRESETS, AREAS_DIR
print(' '.join(a for a in AREA_PRESETS if not (AREAS_DIR / a / 'area.json').exists()))")
    if [[ -n "$missing" ]]; then
      echo "building: $missing"
      # shellcheck disable=SC2086 — one argument per area id
      (cd "$ROOT/backend" && uv run traffic-sim-prepare $missing)
    else
      echo "all areas built"
    fi
    ;;
esac

for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "port $port is already in use (another dev server?). Stop it, or set BACKEND_PORT / FRONTEND_PORT." >&2
    exit 1
  fi
done

step "Starting backend (:$BACKEND_PORT) and frontend (:$FRONTEND_PORT) — Ctrl+C stops both"
pids=()
cleanup() {
  trap - INT TERM EXIT
  kill ${pids[@]+"${pids[@]}"} 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

(cd "$ROOT/backend" && exec uv run traffic-sim-server --port "$BACKEND_PORT" --reload) &
pids+=($!)
(cd "$ROOT/frontend" && BACKEND_URL="http://127.0.0.1:$BACKEND_PORT" exec npm run dev -- --port "$FRONTEND_PORT" --strictPort) &
pids+=($!)

# Exit (and stop the other) as soon as either process dies.
while kill -0 "${pids[0]}" 2>/dev/null && kill -0 "${pids[1]}" 2>/dev/null; do
  sleep 1
done
