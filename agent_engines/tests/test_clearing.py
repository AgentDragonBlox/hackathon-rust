"""Unit tests for clearing.py, fairness.py, and settlement.py — pure logic,
no HTTP. These construct offers/actions directly rather than going through
the API, so a failure here points precisely at the dispatch algorithm
itself, not at request/response wiring.
"""
from shared.contracts import AgentOffer, ProposedAction

from agent_engines.clearing import clear_offers_for_request
from agent_engines.fairness import RecencyTracker
from agent_engines.settlement import build_trade


def _offer(asset_id, kw, cost, request_id="req-1"):
    return AgentOffer(offer_id=f"offer-{asset_id}", request_id=request_id, asset_id=asset_id,
                       offer_type="load_reduction", kw_offered=kw, cost=cost)


def test_merit_order_picks_cheapest_first():
    offers = [_offer("expensive", 50, 10.0), _offer("cheap", 50, 2.0)]
    actions = clear_offers_for_request(offers, 30, RecencyTracker())
    assert len(actions) == 1
    assert actions[0].asset_id == "cheap"
    assert actions[0].kw_amount == 30


def test_partial_fill_on_last_offer_taken():
    offers = [_offer("a", 20, 1.0), _offer("b", 20, 2.0)]
    actions = clear_offers_for_request(offers, 25, RecencyTracker())
    assert actions[0].asset_id == "a" and actions[0].kw_amount == 20
    assert actions[1].asset_id == "b" and actions[1].kw_amount == 5


def test_rejected_offers_are_excluded_from_clearing():
    rejected = AgentOffer(offer_id="r", request_id="req-1", asset_id="x", offer_type="load_reduction",
                           kw_offered=0, cost=0, rejected=True, rejection_reason="test")
    offers = [rejected, _offer("ok", 20, 1.0)]
    actions = clear_offers_for_request(offers, 10, RecencyTracker())
    assert len(actions) == 1
    assert actions[0].asset_id == "ok"


def test_fairness_penalizes_recently_used_asset():
    tracker = RecencyTracker()
    offers = [_offer("cheap", 20, 2.0), _offer("pricier", 20, 3.0)]
    for _ in range(3):
        tracker.record("cheap")
    actions = clear_offers_for_request(offers, 15, tracker)
    # effective price of "cheap" = 2.0 * (1 + 0.3*3) = 3.8, above "pricier"'s 3.0
    assert actions[0].asset_id == "pricier"


def test_build_trade_uses_offer_price():
    offer = _offer("acad-1", 28, 4.2)
    action = ProposedAction(action_id="act-1", request_id="req-1", asset_id="acad-1",
                             action_type="hvac_reduction", kw_amount=28, source_offer_id=offer.offer_id)
    trade = build_trade(action, offer)
    assert trade.price == 4.2
    assert trade.kw_amount == 28
    assert trade.seller_id == "acad-1"
    assert trade.buyer_id == "grid"


def test_build_trade_respects_adjusted_kw_amount():
    # Simulates a partially-approved action: kw_amount has already been
    # overwritten with adjusted_kw_amount before this function ever sees
    # it. This is the exact correctness property flagged in
    # INTEGRATION_GUIDELINES.md's open items — build_trade must record
    # what was actually approved, not the originally proposed amount.
    offer = _offer("acad-1", 28, 4.2)
    action = ProposedAction(action_id="act-1", request_id="req-1", asset_id="acad-1",
                             action_type="hvac_reduction", kw_amount=14, source_offer_id=offer.offer_id)
    trade = build_trade(action, offer)
    assert trade.kw_amount == 14  # NOT the original 28
