"""Region packs: loading, validation and the values regions decide. No SUMO needed."""

import shutil
from pathlib import Path

import pytest

from traffic_sim import region as reg
from traffic_sim.config import REGIONS_DIR
from traffic_sim.signals import is_automated

REGION = """
name = "Testville"
country = "Nowhere"
driving_side = "right"
default_area = "centre"
default_preset = "rush"

[demand]
reference_area = "centre"
reference_main_lane_km = 50.0

[areas.centre]
name = "Centre"
bbox = [10.00, 50.00, 10.02, 50.02]

[areas.whole]
name = "Whole town"
kind = "city"
bbox = [9.90, 49.90, 10.10, 50.10]
"""

PRESETS = """
[[presets]]
id = "rush"
label = "Rush"
start_hour = 8
volume = 4000
through_share = 0.6
mix = { car = 80, bus = 5, motorcycle = 10, truck = 5 }
"""


def make_pack(tmp_path: Path, region: str = REGION, presets: str = PRESETS, signals: str | None = None) -> Path:
    d = tmp_path / "testville"
    d.mkdir()
    (d / "region.toml").write_text(region)
    (d / "presets.toml").write_text(presets)
    if signals is not None:
        (d / "signals.toml").write_text(signals)
    return d


def parse(d: Path):
    region, problems, warnings = reg.parse_region(d)
    return region, problems, warnings


def test_shipped_packs_are_valid():
    regions = reg.all_regions()
    assert "dhaka" in regions
    for d in reg.region_dirs():
        region, problems, _ = parse(d)
        assert region is not None and not problems, problems


def test_template_parses(tmp_path):
    # The template is what new packs are copied from; keep it in step with the schema.
    d = shutil.copytree(REGIONS_DIR / "_template", tmp_path / "example")
    region, problems, _ = parse(d)
    assert region is not None and not problems, problems


def test_minimal_pack(tmp_path):
    region, problems, _ = parse(make_pack(tmp_path))
    assert not problems
    assert region.driving_side == "right" and not region.lefthand
    assert region.areas["whole"].detail == "arterial"  # the default for a city
    assert region.areas["centre"].detail == "full"
    assert region.signals.elsewhere == "on"  # no signals.toml: every signal runs
    assert region.waterlogging_file is None


@pytest.mark.parametrize(
    "change, expected",
    [
        (lambda s: s.replace('"right"', '"middle"'), "driving_side"),
        (lambda s: s.replace("[10.00, 50.00, 10.02, 50.02]", "[50.00, 10.00, 50.02, 10.02]"), None),
        (lambda s: s.replace("[10.00, 50.00, 10.02, 50.02]", "[10.02, 50.00, 10.00, 50.02]"), "west < east"),
        (lambda s: s.replace('default_area = "centre"', 'default_area = "nope"'), "default_area"),
        (lambda s: s.replace('reference_area = "centre"', 'reference_area = "nope"'), "reference_area"),
        (lambda s: s.replace("[areas.centre]", "[areas.Centre]"), "lower-case"),
        (lambda s: s.replace('name = "Testville"\n', ""), "'name'"),
    ],
)
def test_region_problems(tmp_path, change, expected):
    region, problems, _ = parse(make_pack(tmp_path, region=change(REGION)))
    if expected is None:  # a valid (if odd) bbox
        assert not problems
    else:
        assert region is None and any(expected in p for p in problems), problems


@pytest.mark.parametrize(
    "change, expected",
    [
        (lambda s: s.replace("car = 80", "tesla = 80"), "unknown vehicle type 'tesla'"),
        (lambda s: s.replace("through_share = 0.6", "through_share = 1.5"), "through_share"),
        (lambda s: s.replace("volume = 4000", 'volume = "lots"'), "wrong type"),
        (lambda s: s + s, "unique"),
    ],
)
def test_preset_problems(tmp_path, change, expected):
    region, problems, _ = parse(make_pack(tmp_path, presets=change(PRESETS)))
    assert region is None and any(expected in p for p in problems), problems


def test_missing_vehicle_types_warn(tmp_path):
    _, problems, warnings = parse(make_pack(tmp_path))
    assert not problems
    assert any("no cng" in w for w in warnings)


def test_large_neighbourhood_warns(tmp_path):
    big = REGION.replace("[10.00, 50.00, 10.02, 50.02]", "[10.00, 50.00, 10.20, 50.20]")
    _, problems, warnings = parse(make_pack(tmp_path, region=big))
    assert not problems and any("simulate slowly" in w for w in warnings)


def test_bad_toml_is_reported(tmp_path):
    region, problems, _ = parse(make_pack(tmp_path, presets="[[presets]\n"))
    assert region is None and problems[0].startswith("presets.toml:")


def test_duplicate_area_across_regions(tmp_path, monkeypatch):
    shutil.copytree(make_pack(tmp_path), tmp_path / "other")
    monkeypatch.setattr(reg, "region_dirs", lambda root=None: [tmp_path / "other", tmp_path / "testville"])
    reg.all_regions.cache_clear()
    try:
        with pytest.raises(reg.RegionError, match="also in region"):
            reg.all_regions()
    finally:
        reg.all_regions.cache_clear()


SIGNALS = """
elsewhere = "off"
junctions = [{ name = "main_square", lon = 10.01, lat = 50.01, radius = 40 }]

[[corridors]]
name = "high_street"
buffer = 50
points = [[10.000, 50.005], [10.020, 50.005]]

[[zones]]
name = "campus"
add_missing = true
polygon = [[10.015, 50.015], [10.019, 50.015], [10.019, 50.019], [10.015, 50.019]]
"""


def test_signal_policy(tmp_path):
    region, problems, _ = parse(make_pack(tmp_path, signals=SIGNALS))
    assert not problems
    policy = region.signals
    assert is_automated(policy, 10.0101, 50.0101)  # at the junction
    assert is_automated(policy, 10.010, 50.0053)  # ~35 m off the corridor
    assert is_automated(policy, 10.017, 50.017)  # in the zone
    assert not is_automated(policy, 10.005, 50.015)  # none of them
    on = reg.SignalPolicy(elsewhere="on")
    assert is_automated(on, 10.005, 50.015)


def test_dhaka_signal_policy():
    policy = reg.get_region("dhaka").signals
    assert is_automated(policy, 90.3900, 23.7586)  # Farmgate
    assert is_automated(policy, 90.3950, 23.8000)  # Cantonment
    assert not is_automated(policy, 90.3700, 23.7400)  # Dhanmondi: police-directed


def test_signal_names_must_be_unique(tmp_path):
    dup = SIGNALS.replace('name = "campus"', 'name = "main_square"')
    region, problems, _ = parse(make_pack(tmp_path, signals=dup))
    assert region is None and any("names two" in p for p in problems)


def test_area_info_and_preset_demand():
    info = reg.area_info({"id": "farmgate", "kind": "area", "main_lane_km": 129.2})
    assert info["region"] == "dhaka" and info["demand_scale"] == 1.0 and info["through_scale"] == 1.0
    city = reg.area_info({"id": "dhaka", "kind": "city", "main_lane_km": 1292.0})
    assert city["demand_scale"] == 10.0 and city["through_scale"] == 0.25
    rush = reg.get_region("dhaka").preset("morning_rush")
    d = reg.preset_demand(rush, city)
    assert d["volume"] == 90_000 and d["through_share"] == round(rush.through_share * 0.25, 2)
    # Areas prepared before region packs (no "region") still find theirs.
    assert reg.area_info({"id": "mirpur"})["region"] == "dhaka"
