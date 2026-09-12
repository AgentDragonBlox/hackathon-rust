"""EV charging flexibility agent.

GAP WORTH FLAGGING TO THE TEAM: AssetState has no per-vehicle charging
deadline, so "vehicles must reach target SOC by deadline" can't be read
from the wire contract as it stands. This module tracks urgency in its
own internal dict, keyed by asset_id, populated by whoever builds the
demo scenario (Person 3, most likely). If the team decides to add a real
deadline field to AssetState instead, delete EV_SECONDS_UNTIL_DEADLINE
and read it from the asset object directly — nothing else here changes.
"""
import uuid

from shared.contracts import AgentOffer, AssetState, FlexibilityRequest

FLEXIBLE_FRACTION = 0.70
PRICE_PER_KW = 2.80
URGENT_THRESHOLD_SECONDS = 300

EV_SECONDS_UNTIL_DEADLINE: dict[str, float] = {}


def generate_offer(asset: AssetState, request: FlexibilityRequest) -> AgentOffer:
    offer_id = uuid.uuid4().hex[:8]
    deadline = EV_SECONDS_UNTIL_DEADLINE.get(asset.asset_id)

    if deadline is not None and deadline < URGENT_THRESHOLD_SECONDS:
        return AgentOffer(
            offer_id=offer_id, request_id=request.request_id, asset_id=asset.asset_id,
            offer_type="ev_delay", kw_offered=0.0, cost=0.0, rejected=True,
            rejection_reason=f"vehicle must resume charging within {deadline:.0f}s",
        )

    offerable_kw = round(asset.current_load_kw * FLEXIBLE_FRACTION, 3)
    if not asset.online or offerable_kw < 0.5:
        return AgentOffer(
            offer_id=offer_id, request_id=request.request_id, asset_id=asset.asset_id,
            offer_type="ev_delay", kw_offered=0.0, cost=0.0, rejected=True,
            rejection_reason="no active charging session to delay",
        )

    return AgentOffer(
        offer_id=offer_id, request_id=request.request_id, asset_id=asset.asset_id,
        offer_type="ev_delay", kw_offered=offerable_kw, cost=PRICE_PER_KW,
    )
