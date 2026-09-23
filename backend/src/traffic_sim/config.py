"""Paths and static configuration."""

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
AREAS_DIR = BACKEND_DIR / "data" / "areas"

# Areas that `traffic-sim-prepare` knows how to build without extra arguments.
# bbox = (west, south, east, north)
#
# kind "city": the whole city on main roads only (see prepare.DETAIL_FILTERS),
#   so tens of thousands of vehicles still simulate at interactive speed.
# kind "area": one neighbourhood, every street, about 3 × 3 km.
AREA_PRESETS: dict[str, dict] = {
    "dhaka": {
        "name": "Whole city (Dhaka North + South)",
        "kind": "city",
        "detail": "arterial",
        "bbox": (90.330, 23.685, 90.500, 23.885),
    },
    "farmgate": {
        "name": "Farmgate – Karwan Bazar – Shahbag",
        "bbox": (90.380, 23.738, 90.402, 23.763),
    },
    "motijheel": {
        "name": "Motijheel – Gulistan – Paltan",
        "bbox": (90.405, 23.720, 90.428, 23.740),
    },
    "gulshan": {
        "name": "Gulshan 1 – Mohakhali – Banani",
        "bbox": (90.395, 23.775, 90.425, 23.797),
    },
    "mirpur": {
        "name": "Mirpur 1 – Mirpur 10 – Kazipara",
        "bbox": (90.345, 23.790, 90.378, 23.815),
    },
    "dhanmondi": {
        "name": "Dhanmondi – Science Lab – New Market",
        "bbox": (90.365, 23.728, 90.392, 23.758),
    },
    "mohammadpur": {
        "name": "Mohammadpur – Shyamoli – Asad Gate",
        "bbox": (90.350, 23.752, 90.378, 23.780),
    },
    "gabtoli": {
        "name": "Gabtoli – Technical – Kallyanpur",
        "bbox": (90.335, 23.770, 90.370, 23.795),
    },
    "uttara": {
        "name": "Uttara – Airport – Abdullahpur",
        "bbox": (90.385, 23.845, 90.415, 23.882),
    },
    "kuril": {
        "name": "Kuril – Bashundhara – Nadda",
        "bbox": (90.410, 23.795, 90.445, 23.830),
    },
    "rampura": {
        "name": "Rampura – Badda – Hatirjheel",
        "bbox": (90.405, 23.755, 90.440, 23.790),
    },
    "tejgaon": {
        "name": "Tejgaon – Bijoy Sarani – Mohakhali",
        "bbox": (90.383, 23.755, 90.410, 23.778),
    },
    "moghbazar": {
        "name": "Moghbazar – Malibagh – Mouchak",
        "bbox": (90.398, 23.738, 90.425, 23.760),
    },
    "khilgaon": {
        "name": "Khilgaon – Basabo – Kamalapur",
        "bbox": (90.418, 23.725, 90.448, 23.758),
    },
    "old_dhaka": {
        "name": "Old Dhaka – Sadarghat – Chawkbazar",
        "bbox": (90.385, 23.702, 90.418, 23.725),
    },
    "jatrabari": {
        "name": "Jatrabari – Sayedabad – Postogola",
        "bbox": (90.418, 23.688, 90.448, 23.725),
    },
}

DEFAULT_AREA = "farmgate"
