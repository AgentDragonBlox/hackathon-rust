"""Turns confirmed ProposedActions into Trade records — the settlement
step that happens after Person 1 has validated an action is electrically
feasible. Needs the original offer's price, which main.py caches by
offer_id when /agents/offers is called (same pattern as the request cache
for kw_needed — see the stateful-caching note in main.py).
"""
import uuid

from shared.contracts import AgentOffer, ProposedAction, Trade


def build_trade(action: ProposedAction, source_offer: AgentOffer, buyer_id: str = "grid") -> Trade:
    return Trade(
        trade_id=uuid.uuid4().hex[:8],
        request_id=action.request_id,
        buyer_id=buyer_id,
        seller_id=action.asset_id,
        kw_amount=action.kw_amount,
        price=source_offer.cost,
        action_id=action.action_id,
    )
