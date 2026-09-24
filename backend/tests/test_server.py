"""Integration tests: need the farmgate area (`uv run traffic-sim-prepare farmgate`)."""

import pytest
from fastapi.testclient import TestClient

from traffic_sim.config import AREAS_DIR
from traffic_sim.server import app

pytestmark = pytest.mark.skipif(
    not (AREAS_DIR / "farmgate" / "net.net.xml").exists(), reason="farmgate area not prepared"
)

client = TestClient(app)


def test_rest_endpoints():
    areas = client.get("/api/areas").json()
    assert any(a["id"] == "farmgate" for a in areas["areas"])
    net = client.get("/api/areas/farmgate/network").json()
    assert net["type"] == "FeatureCollection" and len(net["features"]) > 100
    assert client.get("/api/areas/../../etc/network").status_code == 404
    assert {v["id"] for v in client.get("/api/vehicle-types").json()} >= {"car", "rickshaw", "cng"}
    assert client.get("/api/presets").json()["presets"]
    regions = client.get("/api/regions").json()
    dhaka = next(r for r in regions["regions"] if r["id"] == "dhaka")
    assert dhaka["presets"] and dhaka["signal_notes"]["off"]
    assert client.get("/api/presets?region=dhaka").json()["default"] == dhaka["default_preset"]
    assert client.get("/api/presets?region=nowhere").status_code == 404
    farmgate = next(a for a in areas["areas"] if a["id"] == "farmgate")
    assert farmgate["region"] == "dhaka" and farmgate["demand_scale"] == 1.0


def test_simulation_streams_frames_and_applies_live_demand():
    with client.websocket_connect("/ws/sim") as ws:
        ws.send_json({
            "type": "start", "area": "farmgate", "speed": 0, "warmup": 60,
            "demand": {"volume": 6000}, "options": {"sublane": True},
        })
        assert ws.receive_json()["state"] == "starting"
        frames = []
        while len(frames) < 40:
            msg = ws.receive_json()
            assert msg["type"] != "error", msg
            if msg["type"] == "frame":
                frames.append(msg)
        last = frames[-1]
        assert not last["warming_up"]
        assert last["stats"]["running"] > 0
        n = len(last["ids"])
        assert n == len(last["lon"]) == len(last["lat"]) == len(last["kind"])
        assert all(90.3 < x < 90.5 for x in last["lon"])
        assert all(23.6 < y < 23.9 for y in last["lat"])
        assert last["stats"]["avg_speed_kmh"] >= 0

        # Demand 0 → no new vehicles depart.
        ws.send_json({"type": "demand", "volume": 0})
        departed = None
        for _ in range(200):
            msg = ws.receive_json()
            if msg["type"] != "frame":
                continue
            d = msg["stats"]["departed"]
            if departed is not None and msg["stats"]["time"] > t0 + 30:
                assert d - departed <= msg["stats"]["waiting"] + 5
                break
            if departed is None and msg["stats"]["waiting"] == 0:
                departed, t0 = d, msg["stats"]["time"]
        ws.send_json({"type": "stop"})


def test_disconnect_releases_simulation_slots():
    from traffic_sim.server import MAX_SIMS

    # More sessions than slots, each dropped mid-run: all must be cleaned up.
    for _ in range(MAX_SIMS + 2):
        with client.websocket_connect("/ws/sim") as ws:
            ws.send_json({"type": "start", "area": "farmgate", "speed": 0, "warmup": 0})
            while True:
                msg = ws.receive_json()
                assert msg["type"] != "error", msg
                if msg["type"] == "frame":
                    break


def test_rickshaw_main_road_rule_changes_where_rickshaws_can_drive():
    from traffic_sim.engine import load_area
    from traffic_sim.vtypes import RICKSHAW_CLASS_ALLOWED, RICKSHAW_CLASS_BANNED

    area = load_area("farmgate")
    main = {
        e.getID() for e in area.net.getEdges()
        if e.getType() in ("highway.trunk", "highway.primary") and not e.getFunction()
    }
    assert main
    allowed = area.pools(RICKSHAW_CLASS_ALLOWED)
    banned = area.pools(RICKSHAW_CLASS_BANNED)
    assert main & set(allowed.origins)
    assert not main & set(banned.origins)
    assert not main & set(banned.dests)


def test_battery_rickshaws_run_with_main_road_ban():
    with client.websocket_connect("/ws/sim") as ws:
        ws.send_json({
            "type": "start", "area": "farmgate", "speed": 0, "warmup": 0,
            "demand": {"volume": 6000, "mix": {"e_rickshaw": 1}},
            "options": {"sublane": False, "rickshaws_on_main_roads": False},
        })
        start = ws.receive_json()
        assert start["options"]["rickshaws_on_main_roads"] is False
        for _ in range(400):
            msg = ws.receive_json()
            assert msg["type"] != "error", msg
            if msg["type"] == "frame" and msg["stats"]["running"] > 20:
                break
        else:
            raise AssertionError("no battery rickshaws on the road")
        assert msg["stats"]["failed_routes"] == 0
        ws.send_json({"type": "stop"})


def test_demand_cap_scales_with_the_area():
    from traffic_sim.engine import load_area
    from traffic_sim.server import MAX_VOLUME_REFERENCE, _demand_from, max_volume

    farmgate = load_area("farmgate")
    assert max_volume(farmgate) == MAX_VOLUME_REFERENCE
    assert _demand_from({"volume": 1e9}, max_volume(farmgate)).volume == MAX_VOLUME_REFERENCE
    assert _demand_from({"volume": -5}, max_volume(farmgate)).volume == 0
    city = AREAS_DIR / "dhaka" / "area.json"
    if city.exists():
        # A whole-city rush-hour preset is far above Farmgate's cap.
        big = max_volume(load_area("dhaka"))
        assert big > 150_000
        assert _demand_from({"volume": 158_000}, big).volume == 158_000
