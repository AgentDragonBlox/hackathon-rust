"""
Real HTTP client for Person 2's Agent/Market Engine service.

Same principle as grid_client.py: this is the adapter boundary,
so any shape mismatches in Person 2's real API get translated here
instead of leaking into the orchestrator's core loop logic.
"""

from __future__ import annotations

import os

import httpx

from shared.contracts import AgentOffer, FlexibilityRequest, GridState, ProposedAction, Trade

AGENT_ENGINE_URL = os.environ.get("AGENT_ENGINE_URL", "http://localhost:8002")
TIMEOUT_SECONDS = 3.0


class AgentClient:
    def __init__(self, base_url: str = AGENT_ENGINE_URL) -> None:
        self.base_url = base_url

    async def get_offers(
        self, grid_state: GridState, requests: list[FlexibilityRequest]
    ) -> list[AgentOffer]:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{self.base_url}/agents/offers",
                json={
                    "grid_state": grid_state.model_dump(mode="json"),
                    "requests": [r.model_dump(mode="json") for r in requests],
                },
            )
            resp.raise_for_status()
            return [AgentOffer.model_validate(o) for o in resp.json()]

    async def clear_market(self, offers: list[AgentOffer]) -> list[ProposedAction]:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{self.base_url}/agents/clear_market",
                json={"offers": [o.model_dump(mode="json") for o in offers]},
            )
            resp.raise_for_status()
            return [ProposedAction.model_validate(a) for a in resp.json()]

    async def settle(self, actions: list[ProposedAction]) -> list[Trade]:
        """Settle grid-validated actions into Trades. Must be called after
        get_offers()/clear_market() for the same request_id, against the
        same running agent-engine instance — see Person 2's INTEGRATION.md."""
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{self.base_url}/agents/settle",
                json=[a.model_dump(mode="json") for a in actions],
            )
            resp.raise_for_status()
            return [Trade.model_validate(t) for t in resp.json()]
