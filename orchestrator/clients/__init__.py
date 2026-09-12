"""
Single switch point: which grid/agent implementation is the orchestrator
currently talking to. Controlled by env vars so you can flip either
independently, at any time, including seconds before a demo, with no
code changes.

    GRID_SOURCE=mock|live   (default: mock)
    AGENT_SOURCE=mock|live  (default: mock)
"""

from __future__ import annotations

import os

from orchestrator.clients.agent_client import AgentClient
from orchestrator.clients.grid_client import GridClient
from orchestrator.clients.mock_agents import MockAgentEngine
from orchestrator.clients.mock_grid import MockGridEngine

# Single shared mock instances so their internal state persists across ticks.
_mock_grid = MockGridEngine()
_mock_agents = MockAgentEngine()


class GridClientAdapter:
    """Presents one interface regardless of mock/live, and normalizes
    the sync mock engine to the same async interface the live client uses."""

    def __init__(self) -> None:
        self.source = os.environ.get("GRID_SOURCE", "mock")
        self._live = GridClient() if self.source == "live" else None

    async def get_state(self):
        if self._live:
            return await self._live.get_state()
        return _mock_grid.get_state()

    async def validate(self, actions):
        if self._live:
            return await self._live.validate(actions)
        return _mock_grid.validate(actions)

    async def inject_fault(self, feeder_id: str, fault_type: str) -> None:
        if self._live:
            await self._live.inject_fault(feeder_id, fault_type)
        else:
            _mock_grid.inject_fault(feeder_id, fault_type)

    async def reset(self) -> None:
        if self._live:
            await self._live.clear_all_faults()
        else:
            _mock_grid.reset()


class AgentClientAdapter:
    def __init__(self) -> None:
        self.source = os.environ.get("AGENT_SOURCE", "mock")
        self._live = AgentClient() if self.source == "live" else None

    async def get_offers(self, grid_state, requests):
        if self._live:
            return await self._live.get_offers(grid_state, requests)
        return _mock_agents.get_offers(grid_state, requests)

    async def clear_market(self, offers):
        if self._live:
            return await self._live.clear_market(offers)
        return _mock_agents.clear_market(offers)

    async def settle(self, actions):
        if self._live:
            return await self._live.settle(actions)
        return _mock_agents.settle(actions)


def get_grid_client() -> GridClientAdapter:
    return GridClientAdapter()


def get_agent_client() -> AgentClientAdapter:
    return AgentClientAdapter()
