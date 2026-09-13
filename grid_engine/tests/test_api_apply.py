"""
Milestone 7 tests for POST /grid/apply -- the physical feedback loop.

This is the endpoint that actually closes the gap flagged throughout
README.md: an accepted, settled action must change what the NEXT
GET /grid/state reports. Unlike /grid/validate (deliberately
non-mutating, see test_api.py), every test here checks a REAL mutation
of server-side state -- so, same discipline as test_api_faults.py, every
test resets via the `client` fixture's teardown so nothing leaks into
other test files that share this module's globals.
"""

import pytest
from fastapi.testclient import TestClient

from grid_engine import api
from grid_engine.api import app
from grid_engine.scenarios import ASSUMED_DISCHARGE_SUSTAIN_HOURS


@pytest.fixture
def client():
    c = TestClient(app)
    yield c
    c.post("/grid/fault/clear_all", json={})  # also resets applied_state -- see api.py


def _action(action_id, asset_id, action_type, kw_amount):
    return {
        "action_id": action_id, "request_id": "r1", "asset_id": asset_id,
        "action_type": action_type, "kw_amount": kw_amount, "source_offer_id": "o1",
    }


def _asset(state: dict, asset_id: str) -> dict:
    return next(a for a in state["assets"] if a["asset_id"] == asset_id)


# --- accepted actions alter grid state --------------------------------------


def test_applied_load_reduction_reduces_served_demand(client):
    before = client.get("/grid/state").json()
    before_kw = _asset(before, "acad-1")["current_load_kw"]

    r = client.post("/grid/apply", json={"actions": [_action("a1", "acad-1", "hvac_reduction", 28.0)]})
    assert r.status_code == 200
    assert r.json() == [{"action_id": "a1", "applied": True, "reason": None}]

    after = client.get("/grid/state").json()
    assert _asset(after, "acad-1")["current_load_kw"] == pytest.approx(before_kw - 28.0, abs=1e-2)


def test_applied_battery_discharge_reduces_soc_and_serves_load(client):
    before = client.get("/grid/state").json()
    battery_before = _asset(before, "batt-1")
    soc_before = battery_before["soc_percent"]

    r = client.post("/grid/apply", json={"actions": [_action("a2", "batt-1", "battery_discharge", 30.0)]})
    assert r.json()[0]["applied"] is True

    after = client.get("/grid/state").json()
    battery_after = _asset(after, "batt-1")
    # SOC actually depletes now -- README's "SOC is not depleted by
    # recorded settlements" gap, closed.
    expected_mwh_drop = (30.0 / 1000.0) * ASSUMED_DISCHARGE_SUSTAIN_HOURS
    expected_soc_drop = expected_mwh_drop / 0.5 * 100.0  # max_e_mwh=0.5 (see grid.py)
    assert battery_after["soc_percent"] == pytest.approx(soc_before - expected_soc_drop, abs=1e-2)
    # and the battery is now actually supplying power (net demand effect)
    assert battery_after["current_gen_kw"] > 0


# --- rejected / never-sent actions do not alter grid state ------------------


def test_never_applied_action_leaves_state_untouched(client):
    before = client.get("/grid/state").json()
    # only /grid/validate was called -- never /grid/apply
    client.post("/grid/validate", json={"actions": [_action("a3", "hosp-1", "load_reduction", 50.0)]})
    after = client.get("/grid/state").json()
    assert before["feeders"] == after["feeders"]
    assert _asset(before, "hosp-1")["current_load_kw"] == _asset(after, "hosp-1")["current_load_kw"]


def test_unknown_asset_is_not_applied_and_does_not_crash(client):
    r = client.post("/grid/apply", json={"actions": [_action("a4", "nope-1", "load_reduction", 10.0)]})
    assert r.status_code == 200
    result = r.json()[0]
    assert result["applied"] is False
    assert "unknown asset_id" in result["reason"]


def test_emergency_reserve_apply_is_a_noop_but_reported_applied(client):
    """Matches scenarios.apply_proposed_action's own no-op semantics --
    'applied' here means 'processed', not 'changed something physically'."""
    before = client.get("/grid/state").json()
    r = client.post("/grid/apply", json={"actions": [_action("a5", "batt-1", "emergency_reserve", 30.0)]})
    assert r.json()[0]["applied"] is True
    after = client.get("/grid/state").json()
    assert before["feeders"] == after["feeders"]


# --- actions cannot be applied twice ----------------------------------------


def test_applying_the_same_action_id_twice_only_takes_effect_once(client):
    action = _action("a6", "acad-1", "hvac_reduction", 20.0)
    r1 = client.post("/grid/apply", json={"actions": [action]})
    assert r1.json()[0]["applied"] is True

    after_first = client.get("/grid/state").json()
    kw_after_first = _asset(after_first, "acad-1")["current_load_kw"]

    r2 = client.post("/grid/apply", json={"actions": [action]})
    result2 = r2.json()[0]
    assert result2["applied"] is False
    assert "already applied" in result2["reason"]

    after_second = client.get("/grid/state").json()
    kw_after_second = _asset(after_second, "acad-1")["current_load_kw"]
    # exactly one 20kW reduction, not two
    assert kw_after_second == pytest.approx(kw_after_first, abs=1e-6)


def test_battery_discharge_applied_twice_only_debits_energy_once(client):
    action = _action("a7", "batt-1", "battery_discharge", 30.0)
    client.post("/grid/apply", json={"actions": [action]})
    soc_after_first = _asset(client.get("/grid/state").json(), "batt-1")["soc_percent"]

    result2 = client.post("/grid/apply", json={"actions": [action]}).json()[0]
    assert result2["applied"] is False

    soc_after_second = _asset(client.get("/grid/state").json(), "batt-1")["soc_percent"]
    assert soc_after_second == pytest.approx(soc_after_first, abs=1e-9)


def test_battery_energy_floors_at_reserve_not_below(client):
    """Repeated large discharges must never push usable energy below the
    battery's own min_e_mwh reserve floor (0.05 MWh here, see grid.py)."""
    for i in range(10):
        client.post("/grid/apply", json={"actions": [_action(f"drain-{i}", "batt-1", "battery_discharge", 250.0)]})
    applied = client.get("/grid/applied_actions").json()
    assert applied["battery_energy_mwh"]["hospital_bess"] == pytest.approx(0.05, abs=1e-9)
    soc = _asset(client.get("/grid/state").json(), "batt-1")["soc_percent"]
    assert soc == pytest.approx(10.0, abs=1e-6)  # 0.05 / 0.5 * 100


# --- reproducibility: reset clears applied state ----------------------------


def test_clear_all_resets_battery_energy_to_baseline(client):
    client.post("/grid/apply", json={"actions": [_action("a8", "batt-1", "battery_discharge", 30.0)]})
    depleted = _asset(client.get("/grid/state").json(), "batt-1")["soc_percent"]
    assert depleted < 72.0

    client.post("/grid/fault/clear_all", json={})
    restored = _asset(client.get("/grid/state").json(), "batt-1")["soc_percent"]
    assert restored == pytest.approx(72.0, abs=1e-6)


def test_applied_actions_audit_log_records_only_real_applications(client):
    client.post("/grid/apply", json={"actions": [_action("a9", "acad-1", "hvac_reduction", 10.0)]})
    client.post("/grid/apply", json={"actions": [_action("a9", "acad-1", "hvac_reduction", 10.0)]})  # dup, ignored
    client.post("/grid/apply", json={"actions": [_action("bad", "nope-1", "load_reduction", 10.0)]})  # never applied

    applied = client.get("/grid/applied_actions").json()
    assert applied["applied_action_ids"] == ["a9"]
    assert len(applied["log"]) == 1
    assert applied["log"][0]["asset_id"] == "acad-1"


# --- battery SOC persists across a tick rebuild -----------------------------


def test_battery_soc_persists_across_replay_step(monkeypatch):
    """The real regression this guards: a discharge settled on hour N must
    still show up in hour N+1's SOC -- api._rebuild_live_state() rebuilds
    `_net` from `_baseline_net` (fixed 72% SOC) on every replay step, so
    without applied_state.py's persistence this would silently reset."""
    from grid_engine.replay import PublicReplay

    monkeypatch.setattr(api, "_replay", PublicReplay())
    api._active_faults.clear()
    api._applied.reset()
    api._seed_battery_energy_from_baseline()
    api._rebuild_live_state()
    client = TestClient(api.app)
    try:
        client.post("/grid/apply", json={"actions": [_action("r1", "batt-1", "battery_discharge", 30.0)]})
        depleted_soc = _asset(client.get("/grid/state").json(), "batt-1")["soc_percent"]
        assert depleted_soc < 72.0

        client.post("/grid/replay/control", json={"action": "step"})
        soc_after_step = _asset(client.get("/grid/state").json(), "batt-1")["soc_percent"]
        assert soc_after_step == pytest.approx(depleted_soc, abs=1e-6)

        client.post("/grid/replay/control", json={"action": "restart"})
        soc_after_restart = _asset(client.get("/grid/state").json(), "batt-1")["soc_percent"]
        assert soc_after_restart == pytest.approx(72.0, abs=1e-6)
    finally:
        api._active_faults.clear()
        api._applied.reset()
        api._seed_battery_energy_from_baseline()
        api._replay = None
        api._rebuild_live_state()
