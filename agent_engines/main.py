"""Person 2's HTTP service.

Run with:  uvicorn agent_engines.main:app --port 8002 --reload

NOTE ON STATEFULNESS: ClearMarketRequest only carries `offers`, not the
original FlexibilityRequest (so it doesn't know kw_needed on its own), and
there's no wire shape at all for "settle these actions" (Trade needs the
original offer's price, which ProposedAction doesn't carry). Rather than
propose contract changes for either, this service caches both
FlexibilityRequests (by request_id) and AgentOffers (by offer_id) in
memory as they pass through /agents/offers, and later endpoints look them
up from there. This means CALL ORDER MATTERS within one run of this
service: offers -> clear_market -> settle, in that order, for a given
request_id. Fine for a single-process demo; flag it to the team if this
ever needs to survive a service restart or run across multiple instances.
"""
from datetime import datetime

from fastapi import FastAPI, HTTPException

from shared.contracts import (
    AgentOffer,
    ClearMarketRequest,
    FlexibilityRequest,
    OffersRequest,
    ProposedAction,
    ReserveContract,
    Trade,
)

from agent_engines.agents import academic_agent, battery_agent, ev_agent, factory_agent, hospital_agent
from agent_engines.clearing import clear_offers_for_request
from agent_engines.fairness import RecencyTracker
from agent_engines.reserve import create_reserve_contract
from agent_engines.settlement import build_trade

app = FastAPI(title="Agent Engine — Person 2")

_recency = RecencyTracker()
_pending_requests: dict[str, FlexibilityRequest] = {}
_offer_cache: dict[str, AgentOffer] = {}

AGENT_FUNCS = {
    "hospital": hospital_agent.generate_offer,
    "academic": academic_agent.generate_offer,
    "ev": ev_agent.generate_offer,
    "battery": battery_agent.generate_offer,
    "factory": factory_agent.generate_offer,
}


@app.post("/agents/offers", response_model=list[AgentOffer])
def get_offers(payload: OffersRequest) -> list[AgentOffer]:
    offers: list[AgentOffer] = []

    for request in payload.requests:
        _pending_requests[request.request_id] = request

        feeder = next(
            (f for f in payload.grid_state.feeders if f.feeder_id == request.feeder_id),
            None,
        )
        # Only bother assets actually connected to the stressed feeder —
        # this is the "targeted, not everyone" behaviour from the pitch.
        connected_ids = set(feeder.connected_assets) if feeder else set()

        for asset in payload.grid_state.assets:
            if asset.asset_id not in connected_ids:
                continue
            agent_func = AGENT_FUNCS.get(asset.asset_type)
            if agent_func is None:
                continue  # e.g. "solar", "factory", "utility" — no agent yet
            offer = agent_func(asset, request)
            _offer_cache[offer.offer_id] = offer
            offers.append(offer)

    return offers


@app.post("/agents/clear_market", response_model=list[ProposedAction])
def clear_market(payload: ClearMarketRequest) -> list[ProposedAction]:
    by_request: dict[str, list[AgentOffer]] = {}
    for offer in payload.offers:
        by_request.setdefault(offer.request_id, []).append(offer)

    actions: list[ProposedAction] = []
    for request_id, offers in by_request.items():
        request = _pending_requests.get(request_id)
        kw_needed = request.kw_needed if request else sum(o.kw_offered for o in offers)
        actions.extend(clear_offers_for_request(offers, kw_needed, _recency))

    return actions


@app.post("/agents/settle", response_model=list[Trade])
def settle(actions: list[ProposedAction]) -> list[Trade]:
    """Call this AFTER Person 1 has validated the proposed actions and
    confirmed which ones actually go ahead. Pass the confirmed subset only
    — this endpoint doesn't re-check feasibility, it just records price.
    """
    trades = []
    for action in actions:
        offer = _offer_cache.get(action.source_offer_id)
        if offer is None:
            raise HTTPException(
                status_code=404,
                detail=f"no cached offer for source_offer_id={action.source_offer_id} "
                       f"— was /agents/offers called first in this server run?",
            )
        trades.append(build_trade(action, offer))
    return trades


@app.post("/agents/reserve", response_model=ReserveContract)
def reserve(asset_id: str, reserved_kw: float, valid_until: datetime, purpose: str) -> ReserveContract:
    """Not currently called by anything else in the pipeline — exists to
    satisfy the contract's stated ownership. Use this if the demo scenario
    ever needs an asset to commit capacity as a standing reserve rather
    than an active trade (e.g. hospital backup battery held for emergency
    use only).
    """
    return create_reserve_contract(asset_id, reserved_kw, valid_until, purpose)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/agents/debug/ev_deadlines")
def set_ev_deadlines(deadlines: dict[str, float]):
    """Push EV urgency data into this running server process.

    This exists because of a real process-boundary issue: EV_SECONDS_UNTIL_
    DEADLINE lives in this service's own memory. Setting it from a client
    script (e.g. Person 3's demo-control script) does nothing unless it's
    pushed through the API like this — mutating the dict from another
    Python process has no effect on THIS process's copy of the module.
    """
    ev_agent.EV_SECONDS_UNTIL_DEADLINE.update(deadlines)
    return {"updated": list(deadlines.keys())}

