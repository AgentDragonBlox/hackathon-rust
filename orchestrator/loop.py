"""
The orchestration tick loop — the heart of the whole system.

Each tick:
  1. Pull GridState from Person 1 (or mock)
  2. If any feeder is predicted to overload and doesn't already have an
     active market open, build a FlexibilityRequest for it
  3. Ask Person 2 (or mock) for offers, then to clear the market
  4. Send the resulting ProposedActions back to Person 1 for validation
  5. Apply only the feasible ones, record Trades + blockchain entries
  6. Log a SystemEvent for every meaningful step (this IS the agent
     activity feed the dashboard renders)
  7. Broadcast the new state to any connected dashboards

Every external call (to Person 1 or Person 2) is wrapped so a timeout
or exception degrades gracefully instead of killing the loop.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from orchestrator.clients import AgentClientAdapter, GridClientAdapter
from orchestrator.logging_config import log
from orchestrator.state import state
from shared.contracts import FlexibilityRequest, GridState, Prediction, Trade

TICK_INTERVAL_SECONDS = 2.0
CALL_TIMEOUT_SECONDS = 3.0
OVERLOAD_THRESHOLD = 80.0
MARKET_COOLDOWN_TICKS = 10  # don't re-open a market for the same feeder for this many ticks


def _estimate_kw_needed(loading_percent: float) -> float:
    """Rough proxy for how much flexibility to request. Not physically
    exact — Person 1's real engine is the source of truth for feasibility;
    this just needs to be a plausible number to kick off the market."""
    return max((loading_percent - 75.0) * 4.0, 20.0)


class OrchestrationLoop:
    def __init__(self, grid_client: GridClientAdapter, agent_client: AgentClientAdapter) -> None:
        self.grid_client = grid_client
        self.agent_client = agent_client
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self) -> None:
        if self._task is None:
            self._running = True
            self._task = asyncio.create_task(self._run_forever())
            log.info("Orchestration loop started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_forever(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception as exc:  # noqa: BLE001 -- loop must never die
                log.exception("Unhandled error in tick loop: %s", exc)
                state.log_event("System", f"Internal orchestrator error: {exc}", "error")
            await asyncio.sleep(TICK_INTERVAL_SECONDS)

    async def _safe_call(self, coro, *, on_fail_source: str, on_fail_message: str):
        try:
            return await asyncio.wait_for(coro, timeout=CALL_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s call failed: %s", on_fail_source, exc)
            state.log_event(on_fail_source, f"{on_fail_message}: {exc}", "error")
            return None

    async def _tick(self) -> None:
        state.tick_count += 1

        grid_state: GridState | None = await self._safe_call(
            self.grid_client.get_state(),
            on_fail_source="Grid Engine",
            on_fail_message="Failed to fetch grid state",
        )
        if grid_state is None:
            await self._broadcast_snapshot()
            return

        # Person 1's real grid_engine confirms GridState.predictions is
        # always [] today — forecasting isn't wired up server-side yet.
        # Derive our own client-side predictions from loading trend so the
        # market-trigger logic below still works against the real service,
        # not just the mock (which does populate predictions itself).
        if not grid_state.predictions:
            grid_state.predictions = self._derive_predictions(grid_state, state.latest_grid_state)

        state.latest_grid_state = grid_state

        # Clear cooldowns for feeders that have recovered.
        recovered = [
            f.feeder_id for f in grid_state.feeders
            if f.loading_percent < OVERLOAD_THRESHOLD and f.feeder_id in state.active_markets
        ]
        for feeder_id in recovered:
            del state.active_markets[feeder_id]
            state.log_event("System", f"Feeder {feeder_id}: back to normal", "info")

        overloaded_predictions = [
            p for p in grid_state.predictions
            if p.predicted_overload and p.feeder_id not in state.active_markets
        ]

        if not overloaded_predictions:
            await self._broadcast_snapshot()
            return

        for prediction in overloaded_predictions:
            state.active_markets[prediction.feeder_id] = state.tick_count
            await self._run_market_for_feeder(grid_state, prediction)

        await self._broadcast_snapshot()

    def _derive_predictions(self, grid_state: GridState, previous: GridState | None) -> list[Prediction]:
        """Client-side fallback forecaster. Person 1's real grid_engine
        always returns predictions=[] today (forecasting not wired up
        server-side yet — confirmed in grid_engine/INTEGRATION.md's known
        gaps). Rather than depend on that landing before the demo, derive
        a simple trend-based prediction from two consecutive loading
        readings, so the market-trigger logic works the same way against
        mock or live grid data."""
        predictions: list[Prediction] = []
        for feeder in grid_state.feeders:
            if feeder.status in ("faulted", "islanded"):
                continue  # already happened, not a "predicted" event
            if feeder.loading_percent < OVERLOAD_THRESHOLD:
                continue

            eta = None
            if previous:
                prev_feeder = next((f for f in previous.feeders if f.feeder_id == feeder.feeder_id), None)
                if prev_feeder:
                    rate_per_tick = feeder.loading_percent - prev_feeder.loading_percent
                    if rate_per_tick > 0:
                        remaining = max(100.0 - feeder.loading_percent, 0.0)
                        eta = (remaining / rate_per_tick) * TICK_INTERVAL_SECONDS

            predictions.append(
                Prediction(feeder_id=feeder.feeder_id, predicted_overload=True, eta_seconds=eta, confidence=0.7)
            )
        return predictions

    async def _run_market_for_feeder(self, grid_state: GridState, prediction) -> None:
        feeder = next((f for f in grid_state.feeders if f.feeder_id == prediction.feeder_id), None)
        loading = feeder.loading_percent if feeder else 100.0

        eta_str = f"{int(prediction.eta_seconds // 60)}m" if prediction.eta_seconds is not None else "unknown"
        state.log_event(
            "Network Agent",
            f"Feeder {prediction.feeder_id} overload predicted in {eta_str}",
            "warning",
        )

        request = FlexibilityRequest(
            request_id=str(uuid.uuid4()),
            feeder_id=prediction.feeder_id,
            kw_needed=_estimate_kw_needed(loading),
            deadline_seconds=prediction.eta_seconds or 120,
            reason=f"Feeder {prediction.feeder_id} predicted overload",
        )
        state.log_event(
            "Clearing Agent",
            f"{request.kw_needed:.0f} kW flexibility requested on {request.feeder_id}",
        )

        offers = await self._safe_call(
            self.agent_client.get_offers(grid_state, [request]),
            on_fail_source="Agent Engine",
            on_fail_message="Failed to fetch offers",
        )
        if offers is None:
            return

        for offer in offers:
            if offer.rejected:
                state.log_event(
                    f"{offer.asset_id.title()} Agent",
                    f"Rejects {offer.offer_type}: {offer.rejection_reason}",
                    "warning",
                )
            else:
                state.log_event(
                    f"{offer.asset_id.title()} Agent",
                    f"Offers {offer.kw_offered:.0f} kW via {offer.offer_type}",
                )

        proposed = await self._safe_call(
            self.agent_client.clear_market(offers),
            on_fail_source="Clearing Agent",
            on_fail_message="Failed to clear market",
        )
        if not proposed:
            state.log_event("Clearing Agent", "No feasible offers to clear", "warning")
            return

        state.log_event("Clearing Agent", "Market cleared")

        validation_results = await self._safe_call(
            self.grid_client.validate(proposed),
            on_fail_source="Grid Engine",
            on_fail_message="Failed to validate actions",
        )
        if validation_results is None:
            return

        results_by_action = {r.action_id: r for r in validation_results}
        feasible_actions = []

        for action in proposed:
            result = results_by_action.get(action.action_id)
            if result is None or not result.feasible:
                reason = result.reason if result else "no validation result"
                state.log_event("Grid Engine", f"Rejected action on {action.asset_id}: {reason}", "warning")
                continue
            if result.adjusted_kw_amount is not None:
                action.kw_amount = result.adjusted_kw_amount
            feasible_actions.append(action)

        if not feasible_actions:
            state.log_event("Clearing Agent", "No actions passed grid validation", "warning")
            return

        trades = await self._safe_call(
            self.agent_client.settle(feasible_actions),
            on_fail_source="Agent Engine",
            on_fail_message="Failed to settle trades",
        )
        if not trades:
            # Fallback: /agents/settle may not be live yet (confirmed 404 as
            # of the last cross-team sync — see INTEGRATION_GUIDELINES_2.md).
            # Rather than silently dropping trades/blockchain entries for the
            # whole demo whenever Person 2's settle endpoint isn't reachable,
            # the orchestrator builds them itself at a flat demo rate. This
            # is clearly logged as a fallback, not passed off as real settle
            # data, so it's visible during debugging/judging if asked.
            state.log_event(
                "Clearing Agent",
                "Agent settle() unavailable \u2014 using orchestrator fallback pricing",
                "warning",
            )
            trades = [
                Trade(
                    trade_id=str(uuid.uuid4()),
                    request_id=action.request_id,
                    buyer_id="grid",
                    seller_id=action.asset_id,
                    kw_amount=action.kw_amount,
                    price=action.kw_amount * 0.1,
                    action_id=action.action_id,
                )
                for action in feasible_actions
            ]

        for trade in trades:
            state.trades.append(trade)
            tx = state.ledger.append("trade_settlement", trade.trade_id)
            state.log_event(
                "Blockchain",
                f"Trade settled: {trade.kw_amount:.0f} kW from {trade.seller_id} (block #{tx.block_number})",
            )

        state.log_event("Grid Engine", "Power-flow validation PASSED")
        new_state = await self._safe_call(
            self.grid_client.get_state(),
            on_fail_source="Grid Engine",
            on_fail_message="Failed to refresh state after actions",
        )
        if new_state:
            new_feeder = next((f for f in new_state.feeders if f.feeder_id == prediction.feeder_id), None)
            if new_feeder:
                state.log_event(
                    "System",
                    f"Feeder {prediction.feeder_id}: {loading:.0f}% \u2192 {new_feeder.loading_percent:.0f}%",
                )
            state.latest_grid_state = new_state

    async def _broadcast_snapshot(self) -> None:
        await state.broadcast({"type": "state_update", "data": state.snapshot()})
