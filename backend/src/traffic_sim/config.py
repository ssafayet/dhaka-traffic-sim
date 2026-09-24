"""Paths and static configuration.

What is specific to a city (its areas, presets, signals, road rules) lives in
region packs under regions/; see region.py.
"""

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
AREAS_DIR = BACKEND_DIR / "data" / "areas"
# Networks with user layout edits (U-turns, added signals); see scenario.py.
VARIANTS_DIR = BACKEND_DIR / "data" / "variants"
REGIONS_DIR = Path(__file__).with_name("regions")
# The region whose default area and presets the app opens with.
DEFAULT_REGION = os.environ.get("TRAFFIC_SIM_REGION", "dhaka")
