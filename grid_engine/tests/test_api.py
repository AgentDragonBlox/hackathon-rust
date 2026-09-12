"""
Milestone 5 tests for grid_engine.api -- the thin FastAPI wrapper Person
3's orchestrator calls. Uses FastAPI's TestClient (in-process, no real
server/port needed) against the same module-level `_net` the real app
would serve from.
"""

import pytest
from fastapi.testclient import TestClient

from grid_engine.api import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_grid_state_shape_matches_contracts_adapter(client):
    r = client.get("/grid/state")
    assert r.status_code == 200
    body = r.json()
    assert len(body["assets"]) == 6
    feeder_ids = {f["feeder_id"] for f in body["feeders"]}
    assert feeder_ids == {"F1", "F2", "F3", "TRANSFORMER"}
    assert body["predictions"] == []
    assert body["active_faults"] == []
    assert body["islands"] == []


def test_grid_state_reflects_known_baseline_loading(client):
    """Same baseline numbers verified directly against contracts_adapter
    (see test_contracts_adapter.py) -- confirms the HTTP layer isn't
    silently transforming anything."""
    r = client.get("/grid/state")
    feeders = {f["feeder_id"]: f for f in r.json()["feeders"]}
    assert feeders["F2"]["status"] == "warning"
    assert feeders["F2"]["loading_percent"] == pytest.approx(93.295719, abs=1e-3)


def test_validate_matches_integration_md_example(client):
    """The exact clear_market response from Person 2's INTEGRATION.md,
    posted to /grid/validate -- both actions should come back feasible
    with no reason/cap needed, same as the direct scenarios.py test."""
    payload = {
        "actions": [
            {
                "action_id": "act-1", "request_id": "req-1", "asset_id": "ev-1",
                "action_type": "ev_delay", "kw_amount": 14.0, "source_offer_id": "offer-ev",
            },
            {
                "action_id": "act-2", "request_id": "req-1", "asset_id": "acad-1",
                "action_type": "hvac_reduction", "kw_amount": 28.0, "source_offer_id": "offer-acad",
            },
        ]
    }
    r = client.post("/grid/validate", json=payload)
    assert r.status_code == 200
    results = r.json()
    assert [res["action_id"] for res in results] == ["act-1", "act-2"]
    assert all(res["feasible"] for res in results)


def test_validate_caps_oversized_request_over_http(client):
    payload = {
        "actions": [
            {
                "action_id": "act-3", "request_id": "req-2", "asset_id": "batt-1",
                "action_type": "battery_discharge", "kw_amount": 500.0, "source_offer_id": "offer-batt",
            },
        ]
    }
    r = client.post("/grid/validate", json=payload)
    assert r.status_code == 200
    result = r.json()[0]
    assert result["feasible"] is True
    assert result["adjusted_kw_amount"] == pytest.approx(250.0)


def test_validate_rejects_action_causing_real_overvoltage_over_http(client):
    payload = {
        "actions": [
            {
                "action_id": "act-4", "request_id": "req-3", "asset_id": "acad-1",
                "action_type": "hvac_reduction", "kw_amount": 240.0, "source_offer_id": "offer-acad-full",
            },
        ]
    }
    r = client.post("/grid/validate", json=payload)
    assert r.status_code == 200
    result = r.json()[0]
    assert result["feasible"] is False
    assert "voltage" in result["reason"]


def test_validate_does_not_mutate_server_side_state(client):
    """Calling /grid/validate must not change what /grid/state reports
    afterwards -- validation is non-mutating by design (see api.py's
    module docstring)."""
    before = client.get("/grid/state").json()
    client.post("/grid/validate", json={"actions": [
        {
            "action_id": "act-5", "request_id": "req-4", "asset_id": "hosp-1",
            "action_type": "load_reduction", "kw_amount": 100.0, "source_offer_id": "offer-hosp",
        },
    ]})
    after = client.get("/grid/state").json()
    assert before["feeders"] == after["feeders"]


def test_validate_empty_actions_list_returns_empty_results(client):
    r = client.post("/grid/validate", json={"actions": []})
    assert r.status_code == 200
    assert r.json() == []


def test_validate_unknown_asset_returns_infeasible_not_5xx(client):
    payload = {
        "actions": [
            {
                "action_id": "act-6", "request_id": "req-5", "asset_id": "nope-1",
                "action_type": "load_reduction", "kw_amount": 10.0, "source_offer_id": "offer-bad",
            },
        ]
    }
    r = client.post("/grid/validate", json=payload)
    assert r.status_code == 200
    result = r.json()[0]
    assert result["feasible"] is False
    assert "unknown asset_id" in result["reason"]
