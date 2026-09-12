"""Academic building flexibility agent — cost-sensitive, no hard reserve
constraint. Same "fraction of load is flexible" assumption as the hospital
agent, just a much larger fraction since HVAC/lab loads are genuinely
interruptible.
"""
import uuid

from shared.contracts import AgentOffer, AssetState, FlexibilityRequest

FLEXIBLE_FRACTION = 0.30
PRICE_PER_KW = 4.20


def generate_offer(asset: AssetState, request: FlexibilityRequest) -> AgentOffer:
    offer_id = uuid.uuid4().hex[:8]
    offerable_kw = round(asset.current_load_kw * FLEXIBLE_FRACTION, 3)

    if not asset.online or offerable_kw < 1.0:
        return AgentOffer(
            offer_id=offer_id, request_id=request.request_id, asset_id=asset.asset_id,
            offer_type="hvac_reduction", kw_offered=0.0, cost=0.0, rejected=True,
            rejection_reason="no meaningful flexible load available",
        )

    return AgentOffer(
        offer_id=offer_id, request_id=request.request_id, asset_id=asset.asset_id,
        offer_type="hvac_reduction", kw_offered=offerable_kw, cost=PRICE_PER_KW,
    )
