"""Road edits: live closures and signal plans, and layout variants (U-turns,
added signals). Integration tests on the farmgate area."""

from collections import Counter

import pytest
from fastapi.testclient import TestClient
from sumolib.geomhelper import positionAtShapeOffset

from traffic_sim import scenario
from traffic_sim.config import AREAS_DIR
from traffic_sim.demand import DemandSettings
from traffic_sim.engine import SimOptions, Simulation, load_area
from traffic_sim.scenario import SignalPlan
from traffic_sim.server import app

pytestmark = pytest.mark.skipif(
    not (AREAS_DIR / "farmgate" / "net.net.xml").exists(), reason="farmgate area not prepared"
)

client = TestClient(app)

# Farmgate roads: Begum Rokeya Sarani is a divided carriageway (two one-way
# edges); the unnamed street is an undivided two-way street.
DIVIDED = ("15491625#1", "1205721991#1")
UNDIVIDED = ("-1016065922", "1016065922")
UNSIGNALISED_JUNCTION = "9353415455"


def lonlat(area, edge, pos):
    x, y = positionAtShapeOffset(area.net.getEdge(edge).getShape(), pos)
    return area.net.convertXY2LonLat(x, y)


@pytest.fixture(scope="module")
def area():
    return load_area("farmgate")


def run(area, steps, **kw):
    sim = Simulation(area, DemandSettings(volume=8000), SimOptions(sublane=False), **kw)
    sim.start()
    for _ in range(steps):
        sim.step(want_frame=False)
    return sim


def test_topology_lists_signals_with_their_programs(area):
    topo = client.get("/api/areas/farmgate/topology").json()
    assert len(topo["junctions"]) > 100
    assert topo["twins"][UNDIVIDED[0]] == UNDIVIDED[1]
    assert topo["signals"]
    for s in topo["signals"]:
        assert all(len(p["state"]) == len(s["links"]) for p in s["phases"])
        assert s["mode"] in ("actuated", "fixed")
    assert client.get("/api/areas/farmgate/topology?variant=nothere").status_code == 404
    assert client.get("/api/areas/farmgate/network?variant=../../areas/x").status_code == 404


def test_closing_a_road_reroutes_traffic_around_it(area):
    sim = run(area, 240)
    try:
        # The road the most vehicles still have ahead of them.
        conn = sim.conn
        ahead = Counter()
        for vid, route in sim.routes.items():
            i = conn.vehicle.getRouteIndex(vid)
            ahead.update(route[i + 1:] if i >= 0 else route)
        edge, n = ahead.most_common(1)[0]
        assert n > 5
        on_it = set(conn.edge.getLastStepVehicleIDs(edge))
        rerouted, stranded = sim.set_closures({edge: None})
        assert rerouted >= n // 2
        # Stranded: no other way to their destination, so they keep their route.
        live = set(conn.vehicle.getIDList())
        still = {v for v, r in sim.routes.items()
                 if v in live and edge in r[conn.vehicle.getRouteIndex(v) + 1:]}
        assert len(still) <= stranded

        entered = set()
        for _ in range(120):
            sim.step(want_frame=False)
            entered |= set(conn.edge.getLastStepVehicleIDs(edge)) - on_it
        assert not entered, "vehicles drove onto a closed road"

        # Reopening restores the lanes' vehicle classes.
        sim.set_closures({})
        assert conn.lane.getAllowed(f"{edge}_0") != ()
        assert not sim.closed_edges
    finally:
        sim.close()


def test_closing_one_lane_keeps_the_road_open(area):
    # A three-lane road that continues from its other lanes.
    edge, onward = next(
        (e, to) for e in area.net.getEdges()
        if e.getLaneNumber() >= 3 and e.getIncoming()
        for to, conns in e.getOutgoing().items()
        if any(c.getFromLane().getIndex() > 0 for c in conns)
    )
    before = next(iter(edge.getIncoming()))
    sim = run(area, 1, closures={edge.getID(): frozenset({0})})
    try:
        conn = sim.conn
        lane0 = f"{edge.getID()}_0"
        assert conn.lane.getAllowed(lane0) == () and conn.lane.getDisallowed(lane0)
        assert edge.getID() not in sim.closed_edges
        assert edge.getID() in conn.simulation.findRoute(before.getID(), onward.getID(), vType="car").edges
    finally:
        sim.close()


def test_signal_plans_apply_and_reset(area):
    tls, signal = next(iter(area.signals.items()))
    plan = scenario.parse_signal(area, tls, {
        "mode": "fixed", "phases": [{"duration": 20 if "G" in p["state"] else 3} for p in signal["phases"]],
    })
    sim = run(area, 1, signals={tls: plan})
    try:
        conn = sim.conn
        assert conn.trafficlight.getProgram(tls).startswith("user")
        logic = next(l for l in conn.trafficlight.getAllProgramLogics(tls) if l.programID == conn.trafficlight.getProgram(tls))
        assert [p.duration for p in logic.phases] == [d for _, d, _, _ in plan.phases]
        sim.set_signal(tls, SignalPlan("off"))
        assert conn.trafficlight.getProgram(tls) == "off"
        sim.set_signal(tls, None)
        assert conn.trafficlight.getProgram(tls) == signal["program_id"]
    finally:
        sim.close()


def test_signal_plans_are_validated(area):
    tls, signal = next(iter(area.signals.items()))
    n = len(signal["phases"])
    with pytest.raises(ValueError):
        scenario.parse_signal(area, "nope", None)
    with pytest.raises(ValueError):
        scenario.parse_signal(area, tls, {"mode": "fixed", "phases": [{"duration": 10}] * (n + 1)})
    with pytest.raises(ValueError):
        scenario.parse_signal(area, tls, {"mode": "fixed", "phases": [{"duration": 0}] * n})
    with pytest.raises(ValueError):
        scenario.parse_signal(area, tls, {"mode": "actuated", "phases": [{"duration": 30, "min": 40, "max": 50}] * n})
    with pytest.raises(ValueError):
        scenario.parse_signal(area, tls, {"mode": "police"})


def test_live_edits_over_the_websocket(area):
    tls, signal = next(iter(area.signals.items()))
    with client.websocket_connect("/ws/sim") as ws:
        ws.send_json({"type": "start", "area": "farmgate", "speed": 0, "warmup": 0,
                      "closures": [{"edge": DIVIDED[0], "lanes": [0]}]})
        assert ws.receive_json()["state"] == "starting"
        ws.send_json({"type": "closures", "closures": [{"edge": DIVIDED[0]}]})
        ws.send_json({"type": "signal", "id": tls, "plan": {"mode": "off"}})
        ws.send_json({"type": "signal", "id": tls, "plan": {"mode": "fixed", "phases": []}})
        seen = {}
        for _ in range(500):
            msg = ws.receive_json()
            assert msg["type"] != "error", msg
            seen.setdefault(msg["type"], msg)
            if {"closures", "signal", "warning"} <= set(seen) and "frame" in seen:
                break
        assert seen["closures"]["closed"] == 1
        assert seen["signal"]["id"] == tls
        assert "phases" in seen["warning"]["message"]
        assert tls in seen["frame"]["signals"]
        ws.send_json({"type": "stop"})


# --- layout variants ---------------------------------------------------------------


def test_uturns_and_new_signals_build_a_routable_network(area):
    lon, lat = lonlat(area, DIVIDED[0], 397)
    lon2, lat2 = lonlat(area, UNDIVIDED[0], 200)
    res = client.post("/api/areas/farmgate/variants", json={
        "uturns": [{"edge": DIVIDED[0], "lon": lon, "lat": lat},
                   {"edge": UNDIVIDED[0], "lon": lon2, "lat": lat2}],
        "signals": [UNSIGNALISED_JUNCTION],
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert [u["kind"] for u in body["uturns"]] == ["divided", "undivided"]
    assert body["uturns"][0]["opposite"] == DIVIDED[1]

    # Same edits, same network.
    again = client.post("/api/areas/farmgate/variants", json={
        "signals": [UNSIGNALISED_JUNCTION],
        "uturns": [{"edge": DIVIDED[0], "lon": lon, "lat": lat},
                   {"edge": UNDIVIDED[0], "lon": lon2, "lat": lat2}],
    }).json()
    assert again["variant"] == body["variant"]

    variant = load_area("farmgate", body["variant"])
    assert UNSIGNALISED_JUNCTION in variant.signals
    roads = client.get(f"/api/areas/farmgate/network?variant={body['variant']}").json()["features"]
    pieces = {f["properties"]["id"] for f in roads if f["properties"].get("base") == DIVIDED[0]}
    assert pieces == {DIVIDED[0], f"{DIVIDED[0]}.ut0"}
    assert any(f["properties"].get("uturn") for f in roads)

    sim = Simulation(variant, DemandSettings(volume=0), SimOptions(sublane=False))
    sim.start()
    try:
        find = sim.conn.simulation.findRoute
        # Through the median gap, both ways.
        assert find(DIVIDED[0], f"{DIVIDED[1]}.ut0", vType="car").edges == (DIVIDED[0], "ut0.gap", f"{DIVIDED[1]}.ut0")
        assert "ut0.gapr" in find(DIVIDED[1], f"{DIVIDED[0]}.ut0", vType="car").edges
        # Turning around mid-street.
        assert find(UNDIVIDED[0], f"{UNDIVIDED[1]}.ut1", vType="car").edges == (UNDIVIDED[0], f"{UNDIVIDED[1]}.ut1")
        for _ in range(30):
            sim.step(want_frame=False)
    finally:
        sim.close()


def test_banning_uturns_at_a_signalised_junction_keeps_its_signal_valid(area):
    tls = next(s["id"] for s in area.topology()["signals"] if area.net.hasNode(s["id"]))
    j = next(j for j in area.topology()["junctions"] if j["id"] == tls)
    assert j["uturns"] > 0
    res = client.post("/api/areas/farmgate/variants", json={"junction_uturns": [{"junction": tls, "allow": False}]})
    assert res.status_code == 200, res.text
    variant = load_area("farmgate", res.json()["variant"])
    assert next(x for x in variant.topology()["junctions"] if x["id"] == tls)["uturns"] == 0
    signal = variant.signals[tls]
    assert all(len(p["state"]) == len(signal["links"]) for p in signal["phases"])


def test_bad_layout_edits_are_refused_with_reasons(area):
    lon, lat = lonlat(area, DIVIDED[0], 2)  # right at the junction
    res = client.post("/api/areas/farmgate/variants", json={
        "uturns": [{"edge": "no-such-road", "lon": lon, "lat": lat}, {"edge": DIVIDED[0], "lon": lon, "lat": lat}],
        "signals": ["no-such-junction"],
    })
    assert res.status_code == 422
    errors = res.json()["errors"]
    assert [(e["kind"], e["index"]) for e in errors] == [("uturn", 0), ("uturn", 1), ("signal", 0)]
    assert "junction" in errors[1]["message"]

    assert client.post("/api/areas/farmgate/variants", json={"uturns": [{"edge": "x", "lon": "nan", "lat": 0}]}).status_code == 422
    assert client.post("/api/areas/farmgate/variants", json={"signals": ["x"] * 1000}).status_code == 422
    assert client.post("/api/areas/nope/variants", json={}).status_code == 404
    assert client.post("/api/areas/farmgate/variants", json={}).json() == {"variant": None, "uturns": []}


def _movements(net, node):
    return {scenario.MOVEMENT.get(c.getDirection(), "straight")
            for e in node.getIncoming() for cs in e.getOutgoing().values() for c in cs}


@pytest.mark.parametrize("rule, allowed", [("straight_left", {"straight", "left"}), ("left", {"left"})])
def test_turn_rules_keep_only_those_movements(area, rule, allowed):
    # The busiest signalised junction: every movement, U-turns included.
    tls = next(s["id"] for s in area.topology()["signals"] if area.net.hasNode(s["id"]))
    assert _movements(area.net, area.net.getNode(tls)) >= {"straight", "left", "right", "uturn"}
    res = client.post("/api/areas/farmgate/variants", json={"junction_turns": [{"junction": tls, "allow": rule}]})
    if res.status_code == 422:
        # Left only can strand an approach that has no left turn; that's refused.
        assert rule == "left" and "no way out" in res.json()["errors"][0]["message"]
        return
    assert res.status_code == 200, res.text
    variant = load_area("farmgate", res.json()["variant"])
    group = area.crossings[tls]
    node = next(n for n in variant.net.getNodes()
                if n.getID() in group or variant.meta["joined"].get(n.getID()) == list(group))
    assert _movements(variant.net, node) <= allowed
    signal = variant.signals[node.getID()]
    assert all(len(p["state"]) == len(signal["links"]) for p in signal["phases"])
    sim = Simulation(variant, DemandSettings(volume=6000), SimOptions(sublane=False))
    sim.start()
    try:
        for _ in range(60):
            sim.step(want_frame=False)
    finally:
        sim.close()


def test_turn_rules_merge_a_split_crossing(area):
    # Crossings of divided roads are mapped as several junctions; the editor
    # treats them as one, and a rule on any of them applies to all.
    group = next(g for g in set(area.crossings.values()) if len(g) == 2)
    res = client.post("/api/areas/farmgate/variants", json={"junction_turns": [{"junction": group[1], "allow": "straight_left"}]})
    if res.status_code == 422:
        pytest.skip(res.json()["errors"][0]["message"])
    variant = load_area("farmgate", res.json()["variant"])
    assert list(group) in variant.meta["joined"].values()
    # One junction now, which still answers to the unedited ids, in the same place.
    ids = {j["id"] for j in variant.topology()["junctions"]}
    assert not ids & set(group)
    merged = next(j for j in variant.topology()["junctions"] if j["group"] == list(group))
    before = [j for j in area.topology()["junctions"] if j["id"] in group]
    assert all(abs(merged["lon"] - b["lon"]) < 5e-4 and abs(merged["lat"] - b["lat"]) < 5e-4 for b in before)


def test_turn_rules_conflict_with_uturn_settings(area):
    j = next(j for j in area.topology()["junctions"] if not j["signal"])
    res = client.post("/api/areas/farmgate/variants", json={
        "junction_turns": [{"junction": j["id"], "allow": "straight_left"}],
        "junction_uturns": [{"junction": j["id"], "allow": True}],
    })
    assert res.status_code == 422
    assert res.json()["errors"][0]["kind"] == "junction_uturn"
    assert client.post("/api/areas/farmgate/variants", json={
        "junction_turns": [{"junction": j["id"], "allow": "everything"}]}).status_code == 422
