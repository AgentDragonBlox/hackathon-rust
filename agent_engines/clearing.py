"""Merit-order dispatch: cheapest offers served first until the requested
relief is met, with a fairness penalty applied to assets that have been
tapped recently — the same real-world mechanism grid operators use for
economic dispatch, with a fairness term added on top.
"""
import uuid

from shared.contracts import AgentOffer, ProposedAction

from agent_engines.fairness import RecencyTracker

FAIRNESS_WEIGHT = 0.3  # effective_price = cost * (1 + FAIRNESS_WEIGHT * recency_count)


def clear_offers_for_request(
    offers: list[AgentOffer], kw_needed: float, recency: RecencyTracker
) -> list[ProposedAction]:
    candidates = [o for o in offers if not o.rejected and o.kw_offered > 1e-9]

    def effective_price(offer: AgentOffer) -> float:
        return offer.cost * (1 + FAIRNESS_WEIGHT * recency.count(offer.asset_id))

    candidates.sort(key=effective_price)

    actions: list[ProposedAction] = []
    remaining = kw_needed
    for offer in candidates:
        if remaining <= 1e-9:
            break
        take = min(offer.kw_offered, remaining)
        actions.append(ProposedAction(
            action_id=uuid.uuid4().hex[:8],
            request_id=offer.request_id,
            asset_id=offer.asset_id,
            action_type=offer.offer_type,
            kw_amount=round(take, 3),
            source_offer_id=offer.offer_id,
        ))
        remaining -= take
        recency.record(offer.asset_id)

    return actions
