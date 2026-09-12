"""Full API surface tests via FastAPI's TestClient — runs in-process, no
real port or subprocess needed, so these are fast and CI-safe (unlike the
manual uvicorn+httpx verification used during development, which is great
for interactive checking but not for an automated suite). This is the
repeatable version of the same checks.

main.py holds module-level state (_recency, _pending_requests,
_offer_cache) that persists across calls by design — for TESTS that means
it also persists across test functions unless explicitly reset, which
would make tests order-dependent and flaky. The autouse fixture below
resets it before every test.
"""
import pytest
from fastapi.testclient import TestClient

import agent_engines.main as main_module
from agent_engines.agents import ev_agent
from agent_engines.fairness import RecencyTracker
from agent_engines.tests.mock_states import ALL_MOCK_REQUESTS
from shared.contracts import ClearMarketRequest

client = TestClient(main_module.app)


@pytest.fixture(autouse=True)
def reset_service_state():
    main_module._recency = RecencyTracker()
    main_module._pending_requests.clear()
    main_module._offer_cache.clear()
    ev_agent.EV_SECONDS_UNTIL_DEADLINE.clear()
    yield


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_full_loop_normal_overload():
    request = ALL_MOCK_REQUESTS["normal_overload"]
    offers = client.post("/agents/offers", json=request.model_dump(mode="json")).json()
    assert len(offers) == 4

    actions = client.post(
        "/agents/clear_market", json=ClearMarketRequest(offers=offers).model_dump(mode="json")
    ).json()
    total = sum(a["kw_amount"] for a in actions)
    assert abs(total - 42.0) < 1e-6

    trades = client.post("/agents/settle", json=actions).json()
    assert len(trades) == len(actions)
    assert abs(sum(t["kw_amount"] for t in trades) - 42.0) < 1e-6


@pytest.mark.parametrize("name", list(ALL_MOCK_REQUESTS.keys()))
def test_full_loop_all_mock_scenarios(name):
    request = ALL_MOCK_REQUESTS[name]
    offers = client.post("/agents/offers", json=request.model_dump(mode="json")).json()
    assert isinstance(offers, list)

    actions = client.post(
        "/agents/clear_market", json=ClearMarketRequest(offers=offers).model_dump(mode="json")
    ).json()
    if actions:
        trades = client.post("/agents/settle", json=actions).json()
        assert len(trades) == len(actions), f"{name}: settle count mismatch"


def test_settle_unknown_offer_returns_404():
    bad = [{"action_id": "x", "request_id": "req-1", "asset_id": "acad-1",
            "action_type": "hvac_reduction", "kw_amount": 5, "source_offer_id": "does-not-exist"}]
    resp = client.post("/agents/settle", json=bad)
    assert resp.status_code == 404


def test_reserve_endpoint():
    resp = client.post("/agents/reserve", params={
        "asset_id": "batt-1", "reserved_kw": 10, "valid_until": "2026-09-13T00:00:00",
        "purpose": "test reserve",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["asset_id"] == "batt-1"
    assert body["reserved_kw"] == 10


def test_ev_debug_deadline_endpoint_affects_offers():
    resp = client.post("/agents/debug/ev_deadlines", json={"ev-2": 100})
    assert resp.status_code == 200

    request = ALL_MOCK_REQUESTS["ev_deadline_imminent"]
    offers = client.post("/agents/offers", json=request.model_dump(mode="json")).json()
    ev_offer = next(o for o in offers if o["asset_id"] == "ev-2")
    assert ev_offer["rejected"] is True


def test_clear_market_rejects_bare_array_shape():
    # Regression test for the exact bug Person 3 hit once already: sending
    # a bare array instead of {"offers": [...]}. Confirms it fails with a
    # clean validation error, not a silent misinterpretation.
    resp = client.post("/agents/clear_market", json=[{"foo": "bar"}])
    assert resp.status_code == 422


def test_offers_only_asks_connected_assets():
    # Confirms the "targeted, not everyone" behavior: an asset NOT listed
    # in the feeder's connected_assets should get no offer at all.
    request = {
        "grid_state": {
            "timestamp": "2026-09-12T00:00:00",
            "assets": [
                {"asset_id": "hosp-1", "asset_type": "hospital", "current_load_kw": 380, "online": True},
                {"asset_id": "acad-1", "asset_type": "academic", "current_load_kw": 240, "online": True},
            ],
            "feeders": [
                {"feeder_id": "F2", "loading_percent": 91, "connected_assets": ["acad-1"], "status": "warning"}
            ],
            "predictions": [],
        },
        "requests": [
            {"request_id": "req-1", "feeder_id": "F2", "kw_needed": 10, "deadline_seconds": 180, "reason": "test"}
        ],
    }
    offers = client.post("/agents/offers", json=request).json()
    asset_ids = {o["asset_id"] for o in offers}
    assert asset_ids == {"acad-1"}  # hosp-1 not connected to F2, must not appear
