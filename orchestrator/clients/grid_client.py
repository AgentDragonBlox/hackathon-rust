"""
Real HTTP client for Person 1's Grid Engine service.

This is the ONLY place that needs to change if Person 1's actual API
shape differs slightly from the agreed contract — translate here,
keep the orchestrator core blind to it.
"""

from __future__ import annotations

import os

import httpx

from shared.contracts import (
    ApplyActionsRequest,
    ApplyResult,
    ClearFaultRequest,
    FaultInjectionRequest,
    GridState,
    ProposedAction,
    ValidateActionsRequest,
    ValidationResult,
)

GRID_ENGINE_URL = os.environ.get("GRID_ENGINE_URL", "http://localhost:8001")
TIMEOUT_SECONDS = 30.0  # Fault searches can require many AC power-flow solves.


class GridClient:
    def __init__(self, base_url: str = GRID_ENGINE_URL) -> None:
        self.base_url = base_url

    async def get_state(self) -> GridState:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{self.base_url}/grid/state")
            resp.raise_for_status()
            return GridState.model_validate(resp.json())

    async def replay(self, action: str | None = None) -> dict:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            if action is None:
                response = await client.get(f"{self.base_url}/grid/replay")
            else:
                response = await client.post(f"{self.base_url}/grid/replay/control", json={"action": action})
            response.raise_for_status()
            return response.json()

    async def validate(self, actions: list[ProposedAction]) -> list[ValidationResult]:
        # Confirmed: /grid/validate expects a WRAPPED object, {"actions": [...]},
        # not a bare array — see ValidateActionsRequest in schemas/contracts.py.
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            payload = ValidateActionsRequest(actions=actions)
            resp = await client.post(
                f"{self.base_url}/grid/validate",
                json=payload.model_dump(mode="json"),
            )
            resp.raise_for_status()
            return [ValidationResult.model_validate(r) for r in resp.json()]

    async def apply(self, actions: list[ProposedAction]) -> list[ApplyResult]:
        """Commit settled actions into the live grid network -- the
        physical feedback loop. Must only be called with actions that
        already passed /grid/validate AND were successfully settled by
        the agent engine (see orchestrator/loop.py's call site)."""
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            payload = ApplyActionsRequest(actions=actions)
            resp = await client.post(
                f"{self.base_url}/grid/apply",
                json=payload.model_dump(mode="json"),
            )
            resp.raise_for_status()
            return [ApplyResult.model_validate(r) for r in resp.json()]

    async def inject_fault(self, feeder_id: str, fault_type: str) -> None:
        # Confirmed real route is /grid/fault (not /grid/inject_fault), body
        # is FaultInjectionRequest — already in shared contracts.py.
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            payload = FaultInjectionRequest(feeder_id=feeder_id, fault_type=fault_type)
            resp = await client.post(
                f"{self.base_url}/grid/fault",
                json=payload.model_dump(mode="json"),
            )
            resp.raise_for_status()

    async def clear_fault(self, feeder_id: str) -> None:
        """feeder_id here is the registry key — "GRID" for grid_outage,
        the original feeder_id for everything else. See
        grid_engine/INTEGRATION.md."""
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            payload = ClearFaultRequest(feeder_id=feeder_id)
            resp = await client.post(
                f"{self.base_url}/grid/fault/clear",
                json=payload.model_dump(mode="json"),
            )
            resp.raise_for_status()

    async def clear_all_faults(self) -> None:
        # Confirmed: no request body for this route.
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.post(f"{self.base_url}/grid/fault/clear_all")
            resp.raise_for_status()
