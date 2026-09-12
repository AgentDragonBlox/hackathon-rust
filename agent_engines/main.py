"""Person 2's HTTP service.

Run with:  uvicorn agent_engine.main:app --port 8002 --reload

NOTE ON STATEFULNESS: ClearMarketRequest only carries `offers`, not the
original FlexibilityRequest (so it doesn't know kw_needed on its own).
Rather than propose a contract change for this, /agents/offers caches each
incoming request by request_id in memory, and /agents/clear_market looks
it up from there. This means call order matters within one run of this
service (offers before clear_market for the same request_id) — fine for
a single-process demo, but flag it to the team if this ever needs to
survive a service restart or run across multiple instances.
"""
from fastapi import FastAPI

from shared.contracts import (
    AgentOffer,
    ClearMarketRequest,
    FlexibilityRequest,
    OffersRequest,
    ProposedAction,
)

from agent_engines.agents import academic_agent, battery_agent, ev_agent, hospital_agent
from agent_engines.clearing import clear_offers_for_request
from agent_engines.fairness import RecencyTracker

app = FastAPI(title="Agent Engine — Person 2")

_recency = RecencyTracker()
_pending_requests: dict[str, FlexibilityRequest] = {}

AGENT_FUNCS = {
    "hospital": hospital_agent.generate_offer,
    "academic": academic_agent.generate_offer,
    "ev": ev_agent.generate_offer,
    "battery": battery_agent.generate_offer,
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
            offers.append(agent_func(asset, request))

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
