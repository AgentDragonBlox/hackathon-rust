"""Factory/production-line flexibility agent.

Added after grid_engine's real topology (grid_engine/INTEGRATION.md)
revealed F3 connects ONLY to a factory-type asset (fac-1) — without this
agent, any predicted overload on F3 had zero possible offers, meaning
that feeder could never be resolved at all. `production_flex` was
already a valid offer_type in shared/contracts.py and grid_engine's
action-type table already handles it (identically to load_reduction/
hvac_reduction) — this agent was the only missing piece.
"""
import uuid

from shared.contracts import AgentOffer, AssetState, FlexibilityRequest

FLEXIBLE_FRACTION = 0.20   # production lines are less flexible than HVAC (30%)
                            # but more than critical hospital load (5%) — an
                            # internal assumption, same caveat as the other
                            # agents: revisit if a real flexible/critical
                            # split ever gets added to AssetState.
PRICE_PER_KW = 6.50         # priced above academic — halting production has
                            # a real cost, reflected here same way hospital's
                            # high price reflects its reluctance.


def generate_offer(asset: AssetState, request: FlexibilityRequest) -> AgentOffer:
    offer_id = uuid.uuid4().hex[:8]
    offerable_kw = round(asset.current_load_kw * FLEXIBLE_FRACTION, 3)

    if not asset.online or offerable_kw < 1.0:
        return AgentOffer(
            offer_id=offer_id, request_id=request.request_id, asset_id=asset.asset_id,
            offer_type="production_flex", kw_offered=0.0, cost=0.0, rejected=True,
            rejection_reason="no meaningful flexible production load available",
        )

    return AgentOffer(
        offer_id=offer_id, request_id=request.request_id, asset_id=asset.asset_id,
        offer_type="production_flex", kw_offered=offerable_kw, cost=PRICE_PER_KW,
    )
