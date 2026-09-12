"""Battery flexibility agent.

GAP WORTH FLAGGING TO THE TEAM: AssetState reports soc_percent (a
percentage) but no absolute capacity_kwh or max_discharge_kw, so there's
no way to convert "20% of charge available" into an actual kW number
without assuming a battery size. This module assumes a fixed campus
battery bank size internally. If Person 1 ever adds real capacity fields
to AssetState, swap these constants for asset.capacity_kwh /
asset.max_discharge_kw and delete the assumption.
"""
import uuid

from shared.contracts import AgentOffer, AssetState, FlexibilityRequest

PRICE_PER_KW = 5.10
DEFAULT_RESERVE_PERCENT = 20.0
ASSUMED_CAPACITY_KWH = 100.0
ASSUMED_MAX_DISCHARGE_KW = 30.0


def generate_offer(asset: AssetState, request: FlexibilityRequest) -> AgentOffer:
    offer_id = uuid.uuid4().hex[:8]
    soc = asset.soc_percent if asset.soc_percent is not None else 0.0
    reserve = asset.min_reserve_percent if asset.min_reserve_percent is not None else DEFAULT_RESERVE_PERCENT

    available_percent = soc - reserve
    if not asset.online or available_percent <= 0:
        return AgentOffer(
            offer_id=offer_id, request_id=request.request_id, asset_id=asset.asset_id,
            offer_type="battery_discharge", kw_offered=0.0, cost=0.0, rejected=True,
            rejection_reason=f"at or below reserve floor ({reserve:.0f}%)",
        )

    available_kwh = (available_percent / 100.0) * ASSUMED_CAPACITY_KWH
    offerable_kw = min(ASSUMED_MAX_DISCHARGE_KW, available_kwh)

    return AgentOffer(
        offer_id=offer_id, request_id=request.request_id, asset_id=asset.asset_id,
        offer_type="battery_discharge", kw_offered=round(offerable_kw, 3), cost=PRICE_PER_KW,
    )
