#!/bin/sh
# Build road networks that aren't in the image or volume yet, then start the server.
#
# PREPARE_AREAS:
#   missing  (default) build areas listed in region packs that have no network yet
#   all      rebuild every listed area (uses cached OSM where present)
#   none     start straight away with whatever is there
set -eu

case "${PREPARE_AREAS:-missing}" in
  none) ;;
  all) traffic-sim-prepare --all ;;
  missing)
    # A failed download shouldn't keep the server down; the areas that did
    # build are served and the rest retry on the next start.
    traffic-sim-prepare --missing || echo "Some areas failed to build; they will be retried on the next start."
    ;;
  *) echo "PREPARE_AREAS must be missing, all or none" >&2; exit 2 ;;
esac

exec "$@"
