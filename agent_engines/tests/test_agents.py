"""Unit tests for each asset agent's generate_offer(). Pure logic tests —
no server, no HTTP, just the agent functions against constructed AssetStates.
"""
from shared.contracts import AssetState, FlexibilityRequest

from agent_engines.agents import academic_agent, battery_agent, ev_agent, factory_agent, hospital_agent

REQUEST = FlexibilityRequest(request_id="req-test", feeder_id="F1", kw_needed=10,
                              deadline_seconds=180, reason="test")


def test_hospital_offers_flexible_fraction():
    asset = AssetState(asset_id="hosp-1", asset_type="hospital", current_load_kw=380)
    offer = hospital_agent.generate_offer(asset, REQUEST)
    assert offer.rejected is False
    assert offer.kw_offered == round(380 * 0.05, 3)
    assert offer.offer_type == "load_reduction"


def test_hospital_rejects_when_load_too_small():
    asset = AssetState(asset_id="hosp-2", asset_type="hospital", current_load_kw=5)
    offer = hospital_agent.generate_offer(asset, REQUEST)
    assert offer.rejected is True
    assert offer.kw_offered == 0.0


def test_hospital_rejects_when_offline():
    asset = AssetState(asset_id="hosp-3", asset_type="hospital", current_load_kw=380, online=False)
    offer = hospital_agent.generate_offer(asset, REQUEST)
    assert offer.rejected is True


def test_academic_offers_flexible_fraction():
    asset = AssetState(asset_id="acad-1", asset_type="academic", current_load_kw=240)
    offer = academic_agent.generate_offer(asset, REQUEST)
    assert offer.rejected is False
    assert offer.kw_offered == round(240 * 0.30, 3)
    assert offer.offer_type == "hvac_reduction"


def test_factory_offers_flexible_fraction():
    asset = AssetState(asset_id="fac-1", asset_type="factory", current_load_kw=150)
    offer = factory_agent.generate_offer(asset, REQUEST)
    assert offer.rejected is False
    assert offer.kw_offered == round(150 * 0.20, 3)
    assert offer.offer_type == "production_flex"


def test_battery_offers_when_above_reserve():
    asset = AssetState(asset_id="batt-1", asset_type="battery", current_load_kw=0,
                        soc_percent=73, min_reserve_percent=20)
    offer = battery_agent.generate_offer(asset, REQUEST)
    assert offer.rejected is False
    assert offer.kw_offered > 0


def test_battery_rejects_at_reserve_floor():
    asset = AssetState(asset_id="batt-2", asset_type="battery", current_load_kw=0,
                        soc_percent=20, min_reserve_percent=20)
    offer = battery_agent.generate_offer(asset, REQUEST)
    assert offer.rejected is True
    assert "reserve" in offer.rejection_reason.lower()


def test_battery_uses_default_reserve_when_unset():
    asset = AssetState(asset_id="batt-3", asset_type="battery", current_load_kw=0, soc_percent=50)
    offer = battery_agent.generate_offer(asset, REQUEST)
    assert offer.rejected is False  # 50% > the 20% default reserve


def test_ev_offers_when_no_urgency_set():
    ev_agent.EV_SECONDS_UNTIL_DEADLINE.clear()
    asset = AssetState(asset_id="ev-1", asset_type="ev", current_load_kw=20)
    offer = ev_agent.generate_offer(asset, REQUEST)
    assert offer.rejected is False
    assert offer.kw_offered == round(20 * 0.70, 3)


def test_ev_rejects_near_deadline():
    ev_agent.EV_SECONDS_UNTIL_DEADLINE["ev-urgent"] = 100
    asset = AssetState(asset_id="ev-urgent", asset_type="ev", current_load_kw=20)
    offer = ev_agent.generate_offer(asset, REQUEST)
    assert offer.rejected is True
    assert "resume" in offer.rejection_reason.lower() or "deadline" in offer.rejection_reason.lower()
    ev_agent.EV_SECONDS_UNTIL_DEADLINE.clear()


def test_ev_rejects_when_no_active_charging():
    ev_agent.EV_SECONDS_UNTIL_DEADLINE.clear()
    asset = AssetState(asset_id="ev-idle", asset_type="ev", current_load_kw=0)
    offer = ev_agent.generate_offer(asset, REQUEST)
    assert offer.rejected is True
