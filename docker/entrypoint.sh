#!/bin/sh
# Build road networks that aren't in the image or volume yet, then start the server.
#
# PREPARE_AREAS:
#   missing  (default) build preset areas that have no network yet
#   all      rebuild every preset area (uses cached OSM where present)
#   none     start straight away with whatever is there
set -eu

case "${PREPARE_AREAS:-missing}" in
  none) ;;
  all) traffic-sim-prepare --all ;;
  missing)
    missing=$(python -c "
from traffic_sim.config import AREA_PRESETS, AREAS_DIR
print(' '.join(a for a in AREA_PRESETS if not (AREAS_DIR / a / 'area.json').exists()))")
    if [ -n "$missing" ]; then
      echo "Building missing areas (downloads OpenStreetMap data): $missing"
      # One argument per area id. A failed download shouldn't keep the server
      # down; the areas that did build are served and the rest retry next start.
      # shellcheck disable=SC2086
      traffic-sim-prepare $missing || echo "Some areas failed to build; they will be retried on the next start."
    fi
    ;;
  *) echo "PREPARE_AREAS must be missing, all or none" >&2; exit 2 ;;
esac

exec "$@"
