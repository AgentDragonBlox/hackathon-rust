"""
Mock Agent/Market Engine — stands in for Person 2's service.

Implements the same interface Person 2's real service will expose:
  - get_offers(grid_state, requests) -> list[AgentOffer]
  - clear_market(offers) -> list[ProposedAction]

Includes a scripted hospital-battery rejection so the "safety-aware"
demo beat works even before Person 2's real agent logic exists.
"""

from __future__ import annotations

import uuid

from schemas.contracts import AgentOffer, FlexibilityRequest, GridState, ProposedAction, Trade

# Which assets can offer what, and roughly how much / at what cost.
# (Purely illustrative numbers for a convincing mock demo.)
_OFFER_MENU = [
    ("ev1", "ev_delay", 15, 0.10),
    ("academic1", "hvac_reduction", 20, 0.08),
    ("factory1", "production_flex", 25, 0.15),
    ("hosp_battery", "battery_discharge", 30, 0.05),  # will often be rejected by grid validation
]


class MockAgentEngine:
    def __init__(self) -> None:
        # Mirrors the real agent engine's statefulness: offers must be
        # fetched before settle() can look up their price/asset by id.
        self._offer_cache: dict[str, AgentOffer] = {}

    def get_offers(self, grid_state: GridState, requests: list[FlexibilityRequest]) -> list[AgentOffer]:
        offers: list[AgentOffer] = []
        for req in requests:
            for asset_id, offer_type, kw, cost in _OFFER_MENU:
                asset = next((a for a in grid_state.assets if a.asset_id == asset_id), None)
                if asset is None or not asset.online:
                    continue

                rejected = False
                rejection_reason = None
                # Scripted safety beat: hospital battery agent self-rejects
                # if its SOC is already close to the emergency reserve floor.
                if (
                    offer_type == "battery_discharge"
                    and asset.min_reserve_percent is not None
                    and asset.soc_percent is not None
                    and asset.soc_percent - (kw / 10) < asset.min_reserve_percent
                ):
                    rejected = True
                    rejection_reason = "emergency reserve constraint"

                offer = AgentOffer(
                    offer_id=str(uuid.uuid4()),
                    request_id=req.request_id,
                    asset_id=asset_id,
                    offer_type=offer_type,
                    kw_offered=kw,
                    cost=cost,
                    rejected=rejected,
                    rejection_reason=rejection_reason,
                )
                self._offer_cache[offer.offer_id] = offer
                offers.append(offer)
        return offers

    def clear_market(self, offers: list[AgentOffer]) -> list[ProposedAction]:
        """Greedy cheapest-first selection per request until kw_needed is covered.

        Real clearing logic (Person 2) can be arbitrarily smarter; this only
        needs to produce something structurally valid for the pipeline to run.
        """
        by_request: dict[str, list[AgentOffer]] = {}
        for offer in offers:
            if offer.rejected:
                continue
            by_request.setdefault(offer.request_id, []).append(offer)

        proposed: list[ProposedAction] = []
        for request_id, request_offers in by_request.items():
            for offer in sorted(request_offers, key=lambda o: o.cost):
                proposed.append(
                    ProposedAction(
                        action_id=str(uuid.uuid4()),
                        request_id=request_id,
                        asset_id=offer.asset_id,
                        action_type=offer.offer_type,
                        kw_amount=offer.kw_offered,
                        source_offer_id=offer.offer_id,
                    )
                )
        return proposed

    def settle(self, actions: list[ProposedAction]) -> list[Trade]:
        """Mirrors the real agent engine's /agents/settle: look up each
        action's price via its cached offer, produce a Trade per action."""
        trades: list[Trade] = []
        for action in actions:
            offer = self._offer_cache.get(action.source_offer_id)
            price = offer.cost if offer else action.kw_amount * 0.1  # fallback demo rate
            trades.append(
                Trade(
                    trade_id=str(uuid.uuid4()),
                    request_id=action.request_id,
                    buyer_id="grid",
                    seller_id=action.asset_id,
                    kw_amount=action.kw_amount,
                    price=price,
                    action_id=action.action_id,
                )
            )
        return trades
