"""
The single in-memory source of truth for the running demo. No database —
restart-to-reset is intentional and fine for a 24-hour MVP.

Only the orchestrator loop (loop.py) writes to this. Everything else
(API routes, WebSocket broadcasts) reads from it.
"""

from __future__ import annotations

import uuid
from collections import deque
from datetime import datetime

from fastapi import WebSocket

from orchestrator.blockchain.ledger import SimulatedLedger
from schemas.contracts import (
    GridState,
    ReserveContract,
    SystemEvent,
    Trade,
)

EVENT_LOG_MAXLEN = 500


class SystemState:
    def __init__(self) -> None:
        self.tick_count: int = 0
        self.latest_grid_state: GridState | None = None
        self.trades: list[Trade] = []
        self.reserve_contracts: list[ReserveContract] = []
        self.event_log: deque[SystemEvent] = deque(maxlen=EVENT_LOG_MAXLEN)
        self.ledger = SimulatedLedger()

        # feeder_id -> tick when an active market was opened for it, so we
        # don't re-trigger a new market every single tick while a feeder
        # stays in overload/warning.
        self.active_markets: dict[str, int] = {}

        self._connections: list[WebSocket] = []

    # ---- event logging -----------------------------------------------

    def log_event(self, source: str, message: str, severity: str = "info") -> SystemEvent:
        event = SystemEvent(
            event_id=str(uuid.uuid4()),
            timestamp=datetime.utcnow(),
            source=source,
            message=message,
            severity=severity,  # type: ignore[arg-type]
        )
        self.event_log.append(event)
        return event

    # ---- websocket connections -----------------------------------------

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._connections:
            self._connections.remove(ws)

    async def broadcast(self, message: dict) -> None:
        dead = []
        for ws in self._connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    # ---- snapshot for REST + reconnect -----------------------------------

    def snapshot(self) -> dict:
        return {
            "tick_count": self.tick_count,
            "grid_state": self.latest_grid_state.model_dump(mode="json") if self.latest_grid_state else None,
            "trades": [t.model_dump(mode="json") for t in self.trades[-20:]],
            "reserve_contracts": [c.model_dump(mode="json") for c in self.reserve_contracts],
            "blockchain": [tx.model_dump(mode="json") for tx in self.ledger.all_transactions()[-20:]],
            "recent_events": [e.model_dump(mode="json") for e in list(self.event_log)[-50:]],
        }

    def reset(self) -> None:
        self.tick_count = 0
        self.latest_grid_state = None
        self.trades = []
        self.reserve_contracts = []
        self.event_log.clear()
        self.ledger.reset()
        self.active_markets = {}


# module-level singleton — imported wherever state is needed
state = SystemState()
