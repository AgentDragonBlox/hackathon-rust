"""Hospital flexibility agent.

GAP WORTH FLAGGING TO THE TEAM: AssetState has no field for "how much of
this hospital's load is critical vs. sheddable" — only current_load_kw as
a single number. Rather than propose a contract change for one asset type,
this treats it as Person 2's own domain knowledge: a fixed fraction of
hospital load is assumed non-critical (admin lighting, non-clinical HVAC),
the rest is untouchable no matter what. Revisit this constant if Person 1
ever exposes a real critical/flexible split.
"""
import uuid

from shared.contracts import AgentOffer, AssetState, FlexibilityRequest

FLEXIBLE_FRACTION = 0.05   # only ~5% of hospital load is ever offerable
PRICE_PER_KW = 9.50        # priced high on purpose — hospital doesn't want to be picked


def generate_offer(asset: AssetState, request: FlexibilityRequest) -> AgentOffer:
    offer_id = uuid.uuid4().hex[:8]
    offerable_kw = round(asset.current_load_kw * FLEXIBLE_FRACTION, 3)

    if not asset.online or offerable_kw < 1.0:
        return AgentOffer(
            offer_id=offer_id, request_id=request.request_id, asset_id=asset.asset_id,
            offer_type="load_reduction", kw_offered=0.0, cost=0.0, rejected=True,
            rejection_reason="no meaningful non-critical load available to shed",
        )

    return AgentOffer(
        offer_id=offer_id, request_id=request.request_id, asset_id=asset.asset_id,
        offer_type="load_reduction", kw_offered=offerable_kw, cost=PRICE_PER_KW,
    )
