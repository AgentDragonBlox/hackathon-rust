"""
Milestone 6 tests for the fault-injection endpoints in grid_engine.api --
POST /grid/fault, POST /grid/fault/clear, POST /grid/fault/clear_all.
Unlike /grid/validate, these are the one part of this service that's
genuinely stateful/mutating -- these tests check that GET /grid/state
keeps reflecting an injected fault until it's cleared, and that clearing
one fault never disturbs another still-active one.

IMPORTANT: grid_engine.api holds its fault registry at module level, so
tests share state across the module the way the real server would across
requests. Every test clears all faults at the end (via a fixture) so
tests don't leak state into each other.
"""

import pytest
from fastapi.testclient import TestClient

from grid_engine.api import app


@pytest.fixture
def client():
    c = TestClient(app)
    yield c
    c.post("/grid/fault/clear_all", json={})  # always leave the registry clean


def _feeder(state: dict, feeder_id: str) -> dict:
    return next(f for f in state["feeders"] if f["feeder_id"] == feeder_id)


def test_inject_line_fault_reflected_in_state_and_persists(client):
    r = client.post("/grid/fault", json={"feeder_id": "F1", "fault_type": "line_fault"})
    assert r.status_code == 200
    injected = r.json()
    assert injected["active_faults"] == ["F1"]
    assert _feeder(injected, "F1")["status"] == "faulted"
    assert injected["islands"] == [["batt-1", "hosp-1"]]

    # GET /grid/state must keep showing it -- this is the whole point of
    # Milestone 6 being stateful, unlike /grid/validate.
    r2 = client.get("/grid/state")
    state = r2.json()
    assert state["active_faults"] == ["F1"]
    assert _feeder(state, "F1")["status"] == "faulted"


def test_clear_fault_restores_baseline(client):
    baseline = client.get("/grid/state").json()
    client.post("/grid/fault", json={"feeder_id": "F1", "fault_type": "line_fault"})
    r = client.post("/grid/fault/clear", json={"feeder_id": "F1"})
    assert r.status_code == 200
    cleared = r.json()
    assert cleared["active_faults"] == []
    assert cleared["islands"] == []
    assert _feeder(cleared, "F1")["loading_percent"] == pytest.approx(_feeder(baseline, "F1")["loading_percent"])


def test_clear_nonexistent_fault_returns_404(client):
    r = client.post("/grid/fault/clear", json={"feeder_id": "F1"})
    assert r.status_code == 404


def test_invalid_fault_combo_returns_400_and_is_not_registered(client):
    r = client.post("/grid/fault", json={"feeder_id": "F1", "fault_type": "solar_drop"})
    assert r.status_code == 400
    # a rejected combo must not end up "active" -- confirm via /grid/state
    state = client.get("/grid/state").json()
    assert state["active_faults"] == []


def test_unrecognized_fault_type_returns_422(client):
    """FaultInjectionRequest's fault_type is a pydantic Literal --
    FastAPI/pydantic should reject an unknown value before it even
    reaches grid_engine's own logic."""
    r = client.post("/grid/fault", json={"feeder_id": "F1", "fault_type": "meteor_strike"})
    assert r.status_code == 422


def test_two_independent_faults_on_different_feeders_both_persist(client):
    client.post("/grid/fault", json={"feeder_id": "F1", "fault_type": "line_fault"})
    r = client.post("/grid/fault", json={"feeder_id": "F3", "fault_type": "demand_spike"})
    state = r.json()
    assert set(state["active_faults"]) == {"F1", "F3"}
    assert _feeder(state, "F1")["status"] == "faulted"
    assert _feeder(state, "F3")["status"] in ("warning", "overloaded")

    # clearing one must not disturb the other
    r2 = client.post("/grid/fault/clear", json={"feeder_id": "F1"})
    state2 = r2.json()
    assert state2["active_faults"] == ["F3"]
    assert _feeder(state2, "F1")["status"] == "normal"
    assert _feeder(state2, "F3")["status"] in ("warning", "overloaded")


def test_injecting_a_new_fault_on_same_feeder_replaces_not_stacks(client):
    r1 = client.post("/grid/fault", json={"feeder_id": "F2", "fault_type": "demand_spike"})
    demand_spike_loading = _feeder(r1.json(), "F2")["loading_percent"]

    r2 = client.post("/grid/fault", json={"feeder_id": "F2", "fault_type": "solar_drop"})
    state2 = r2.json()
    assert state2["active_faults"] == ["F2"]  # still just one entry for F2, not two
    solar_drop_loading = _feeder(state2, "F2")["loading_percent"]
    assert solar_drop_loading != pytest.approx(demand_spike_loading)


def test_grid_outage_blacks_out_the_whole_campus_via_http(client):
    r = client.post("/grid/fault", json={"feeder_id": "F2", "fault_type": "grid_outage"})
    assert r.status_code == 200
    state = r.json()
    assert state["active_faults"] == ["GRID"]
    assert len(state["islands"]) == 1
    assert set(state["islands"][0]) == {"hosp-1", "acad-1", "ev-1", "fac-1", "solar-1", "batt-1"}
    for feeder in state["feeders"]:
        assert feeder["status"] == "islanded"
        assert feeder["loading_percent"] == 0.0
    for asset in state["assets"]:
        assert asset["current_load_kw"] == 0.0
        assert asset["current_gen_kw"] == 0.0


def test_clear_all_removes_every_fault(client):
    client.post("/grid/fault", json={"feeder_id": "F1", "fault_type": "line_fault"})
    client.post("/grid/fault", json={"feeder_id": "F3", "fault_type": "demand_spike"})
    r = client.post("/grid/fault/clear_all", json={})
    state = r.json()
    assert state["active_faults"] == []
    assert state["islands"] == []


def test_validate_still_works_and_is_non_mutating_alongside_active_faults(client):
    """/grid/validate must keep working (against the current, possibly
    faulted, live state) and must still not mutate anything -- a fault
    stays active across a validate call the same way it persists across
    a plain GET."""
    client.post("/grid/fault", json={"feeder_id": "F3", "fault_type": "demand_spike"})
    before = client.get("/grid/state").json()
    client.post("/grid/validate", json={"actions": [
        {"action_id": "a1", "request_id": "r1", "asset_id": "hosp-1",
         "action_type": "load_reduction", "kw_amount": 50.0, "source_offer_id": "o1"},
    ]})
    after = client.get("/grid/state").json()
    before.pop("timestamp")
    after.pop("timestamp")  # each build_grid_state() call stamps its own timestamp -- not a state diff
    assert before == after
