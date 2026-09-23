"""Road features and weather: bus stops, stands, crossings, hot zones,
waterlogging, rain, timed closures, breakdowns and demand response.
Integration tests on the farmgate area."""

import pytest
from fastapi.testclient import TestClient
from sumolib.geomhelper import positionAtShapeOffset

from traffic_sim.config import AREAS_DIR
from traffic_sim.demand import DemandSettings
from traffic_sim.engine import SimOptions, Simulation, load_area
from traffic_sim.features import WATER_DEPTH, parse_features
from traffic_sim.scenario import ClosureRule
from traffic_sim.server import app

pytestmark = pytest.mark.skipif(
    not (AREAS_DIR / "farmgate" / "net.net.xml").exists(), reason="farmgate area not prepared"
)

client = TestClient(app)


@pytest.fixture(scope="module")
def area():
    return load_area("farmgate")


def point_on(area, edge, frac=0.5):
    e = area.net.getEdge(edge)
    x, y = positionAtShapeOffset(e.getShape(), e.getLength() * frac)
    lon, lat = area.net.convertXY2LonLat(x, y)
    return {"lon": lon, "lat": lat}


def busy_edge(area, lanes=1, min_length=80, vclass="bus"):
    """A long road with roads leading in and out of it."""
    return next(
        e.getID() for e in sorted(area.net.getEdges(), key=lambda e: -e.getLength())
        if not e.getFunction() and e.getLaneNumber() >= lanes and e.getLength() >= min_length and e.allows(vclass)
        and e.getIncoming() and e.getOutgoing()
    )


def sim_with(area, features=None, volume=6000, **opts):
    sim = Simulation(area, DemandSettings(volume=volume), SimOptions(sublane=False, **opts), features=features)
    sim.start()
    return sim


def test_defaults_include_osm_bus_stops_and_news_waterlogging():
    d = client.get("/api/areas/farmgate/features").json()
    names = {w["name"] for w in d["water"]}
    assert {"Green Road", "Karwan Bazar", "Farmgate"} <= names
    assert all(w["sources"] and w["sources"][0]["url"].startswith("https://") for w in d["water"])
    assert all(w["depth"] in WATER_DEPTH for w in d["water"])
    assert isinstance(d["bus_stops"], list)


def test_points_snap_to_roads_and_bad_input_is_refused(area):
    edge = busy_edge(area)
    f = parse_features(area, {"bus_stops": [{"id": "b1", **point_on(area, edge), "dwell": 40}]})
    assert f.bus_stops[0].at.edge in (edge, area.twins.get(edge))
    far = parse_features(area, {"bus_stops": [{"id": "b2", "lon": 90.0, "lat": 23.0}]})
    assert not far.bus_stops and far.warnings
    for bad in (
        {"bus_stops": [{"id": "x", "lon": "90", "lat": 23.7}]},
        {"stands": [{"id": "s", **point_on(area, edge), "kind": "car"}]},
        {"water": [{"id": "w", **point_on(area, edge), "depth": "ocean"}]},
        {"weather": {"rain": "monsoon"}},
        {"bus_stops": [{"id": "bad id!", **point_on(area, edge)}]},
        {"bus_stops": [{"id": str(i), "lon": 90.39, "lat": 23.75} for i in range(501)]},
    ):
        with pytest.raises(ValueError):
            parse_features(area, bad)


def test_buses_stop_at_bus_stops(area):
    edge = busy_edge(area)
    f = parse_features(area, {"bus_stops": [{"id": "b1", **point_on(area, edge), "dwell": 30}]})
    stop_edge = f.bus_stops[0].at.edge
    sim = sim_with(area, f, volume=0)
    try:
        conn = sim.conn
        before = next(iter(area.net.getEdge(stop_edge).getIncoming())).getID()
        conn.route.add("rb", [before, stop_edge])
        conn.vehicle.add("bus1", "rb", typeID="bus", depart="now")
        sim._add_stops("bus1", "bus", (before, stop_edge))
        stopped_on = set()
        for _ in range(240):
            sim.step(want_frame=False)
            if "bus1" in conn.vehicle.getIDList() and conn.vehicle.isStopped("bus1"):
                stopped_on.add(conn.vehicle.getRoadID("bus1"))
        assert stop_edge in stopped_on
    finally:
        sim.close()


def test_stands_park_vehicles_that_are_not_counted_as_traffic(area):
    edge = busy_edge(area, lanes=2, vclass="passenger")
    raw = {"stands": [{"id": "s1", **point_on(area, edge), "kind": "cng", "parked": 3}]}
    sim = sim_with(area, parse_features(area, raw), volume=0)
    try:
        for _ in range(5):
            frame = sim.step()
        parked = sim._parked["s1"]
        assert len(parked) == 3
        assert frame["stats"]["running"] == 0  # parked vehicles aren't traffic
        assert set(map(int, parked)) <= set(frame["ids"])  # but they are drawn
        sim.set_features(parse_features(area, {}))
        sim.step()
        assert not set(parked) & set(sim.conn.vehicle.getIDList())
    finally:
        sim.close()


def test_crossings_hold_up_every_lane_then_clear(area):
    edge = busy_edge(area, lanes=2, vclass="passenger")
    raw = {"crossings": [{"id": "c1", **point_on(area, edge), "every": 10, "duration": 15}]}
    sim = sim_with(area, parse_features(area, raw), volume=0)
    try:
        seen = 0
        for _ in range(20):
            frame = sim.step()
            if "c1" in frame.get("crossing", []):
                seen = max(seen, len(sim._crowds))
        assert seen >= area.edge_lanes[sim.features.crossings[0].at.edge]
        assert not set(map(str, frame["ids"])) & sim._crowds  # never drawn as vehicles
        for _ in range(60):
            sim.step(want_frame=False)
        sim.set_features(parse_features(area, {}))
        for _ in range(30):
            sim.step(want_frame=False)
        assert not sim._crowds
    finally:
        sim.close()


def test_flooding_slows_traffic_and_keeps_cars_out_of_deep_water(area):
    edge = busy_edge(area, vclass="passenger")
    zone = {"id": "w1", **point_on(area, edge), "radius": 60, "depth": "deep"}
    sim = sim_with(area, parse_features(area, {"water": [zone], "weather": {"rain": "none"}}), volume=0)
    try:
        lane = f"{edge}_0"
        assert "passenger" in sim.conn.lane.getAllowed(lane)  # no rain, no flood
        sim.set_features(parse_features(area, {"water": [zone], "weather": {"rain": "heavy"}}))
        assert "passenger" not in sim.conn.lane.getAllowed(lane)
        assert "bus" in sim.conn.lane.getAllowed(lane)
        assert sim.conn.lane.getMaxSpeed(lane) == pytest.approx(WATER_DEPTH["deep"][0])
        assert edge in sim.blocked["passenger"] and edge not in sim.blocked["bus"]
        assert sim.conn.vehicletype.getSpeedFactor("car") < 1
        sim.set_features(parse_features(area, {"water": [zone], "weather": {"rain": "heavy", "flooding": "off"}}))
        assert "passenger" in sim.conn.lane.getAllowed(lane)
        assert sim.conn.lane.getMaxSpeed(lane) == pytest.approx(area.lane_speed[lane])
    finally:
        sim.close()


def test_hot_zones_draw_trips_and_vendors_take_the_kerb_lane(area):
    edge = busy_edge(area, lanes=2, vclass="passenger")
    zone = {"id": "h1", **point_on(area, edge), "radius": 150, "trips": 10, "vendors": True}
    f = parse_features(area, {"hot_zones": [zone]})
    inside = f.hot_zones[0].edges
    sim = sim_with(area, f, volume=0)
    try:
        assert "passenger" not in sim.conn.lane.getAllowed(f"{edge}_0")
        assert "passenger" in sim.conn.lane.getAllowed(f"{edge}_1")
        gen = sim.generator
        def share():
            trips = [o for _ in range(300) for _, o, d, t in gen.trips_for_step(DemandSettings(volume=36000, through_share=0), 1)]
            return sum(o in inside for o in trips) / len(trips)
        with_zone = share()
        gen.set_edge_weights({})
        assert with_zone > 2 * share()
    finally:
        sim.close()


def test_timed_closures_follow_the_clock(area):
    edge = busy_edge(area, vclass="passenger")
    start = 8 * 3600
    rule = ClosureRule(edge, None, start + 20, start + 40)  # 8:00:20–8:00:40
    sim = Simulation(area, DemandSettings(volume=0), SimOptions(sublane=False, start_hour=8), closures=[rule])
    sim.start()
    try:
        states = []
        for _ in range(60):
            sim.step(want_frame=False)
            states.append(edge in sim.closed_edges)
        assert not states[5] and any(states[20:45]) and not states[-1]
    finally:
        sim.close()


def test_breakdowns_and_demand_response(area):
    f = parse_features(area, {"breakdowns": 50, "elasticity": 0.5})
    sim = sim_with(area, f, volume=8000)
    try:
        # 50 per hour per 100 km of road: several in 10 minutes on Farmgate.
        for _ in range(600):
            sim.step(want_frame=False)
        assert sim.total_breakdowns >= 1
        for vid in sim.broken:
            assert sim.conn.vehicle.isStopped(vid) or sim.conn.vehicle.getStops(vid)
        sim._delay_min = 5
        sim._inject()
        assert sim.demand_factor == pytest.approx(0.75)
    finally:
        sim.close()


def test_features_over_the_websocket(area):
    edge = busy_edge(area)
    feats = {
        "bus_stops": [{"id": "b1", **point_on(area, edge)}],
        "stands": [{"id": "s1", **point_on(area, edge, 0.7), "kind": "e_rickshaw", "parked": 2}],
        "weather": {"rain": "light"},
    }
    with client.websocket_connect("/ws/sim") as ws:
        ws.send_json({"type": "start", "area": "farmgate", "speed": 0, "warmup": 0, "features": feats,
                      "options": {"sublane": False, "reroute_every": 120}})
        assert ws.receive_json()["state"] == "starting"
        ws.send_json({"type": "features", "features": {**feats, "bus_stops": [{"id": "far", "lon": 90.0, "lat": 23.0}]}})
        seen = {}
        for _ in range(400):
            msg = ws.receive_json()
            assert msg["type"] != "error", msg
            seen.setdefault(msg["type"], msg)
            if {"features", "warning", "frame"} <= set(seen):
                break
        assert "isn't next to a road" in seen["warning"]["message"]
        assert "demand_factor" in seen["frame"]["stats"]
        ws.send_json({"type": "stop"})
