"""
FastAPI app for the orchestrator. Run with:

    uvicorn orchestrator.main:app --reload --port 8000

Endpoints:
  GET  /state/snapshot        current SystemState, for load/reconnect
  WS   /ws/dashboard           live push of events + state updates
  POST /scenario/{name}        trigger a demo scenario
  POST /scenario/reset         reset to normal state
  GET  /health                 orchestrator + upstream health summary
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
import httpx
from pydantic import BaseModel

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from orchestrator.clients import get_agent_client, get_grid_client
from orchestrator.loop import OrchestrationLoop
from orchestrator.logging_config import log
from orchestrator.scenarios import SCENARIOS
from orchestrator.state import state

grid_client = get_grid_client()
agent_client = get_agent_client()
loop = OrchestrationLoop(grid_client, agent_client)


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop.start()
    log.info("Orchestrator startup complete (grid=%s, agents=%s)", grid_client.source, agent_client.source)
    yield
    await loop.stop()


app = FastAPI(title="Microgrid Resilience Exchange — Orchestrator", lifespan=lifespan)

# Wide open for a hackathon demo — tighten if this ever leaves localhost.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/state/snapshot")
async def get_snapshot():
    return state.snapshot()


EXPERIMENT_RESULTS_PATH = Path(__file__).resolve().parents[1] / "data" / "experiment_results.json"


@app.get("/experiment/results")
async def get_experiment_results():
    """Task 3/4/5: serves the precomputed no-intervention / load-shedding /
    market-system comparison (scripts/run_experiment.py's output) for the
    dashboard's Scenario Comparison panel. Read from disk on every request
    (not cached at import time) so rerunning the script and refreshing the
    dashboard picks up new numbers without restarting the orchestrator.
    Static and offline by design -- this is NOT a live re-run of the
    168-hour comparison on every page load, which would make the demo's
    timing depend on whether Rust happens to be reachable at that moment."""
    if not EXPERIMENT_RESULTS_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="No experiment results yet -- run `python scripts/run_experiment.py` from the project root.",
        )
    return json.loads(EXPERIMENT_RESULTS_PATH.read_text(encoding="utf-8"))


@app.get("/health")
async def health():
    return {
        "orchestrator": "ok",
        "tick_count": state.tick_count,
        "grid_source": grid_client.source,
        "agent_source": agent_client.source,
    }


@app.post("/scenario/reset")
async def reset_scenario():
    async with loop.control_lock:
        await grid_client.reset()
        state.reset()
        loop._last_replay_index = None
        state.log_event("System", "Injected faults cleared; current dataset sample retained")
    return {"status": "reset"}


@app.post("/scenario/{name}")
async def trigger_scenario(name: str):
    scenario = SCENARIOS.get(name)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"Unknown scenario '{name}'")

    async with loop.control_lock:
        try:
            await grid_client.inject_fault(scenario["feeder_id"], scenario["fault_type"])
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Grid fault control failed: {exc}") from exc
        state.active_markets.pop(scenario["feeder_id"], None)
        loop._last_replay_index = None
        state.log_event("System", f"Simulated fault injected: {name}")
    return {"status": "triggered", "scenario": name}


class ReplayCommand(BaseModel):
    action: Literal["play", "pause", "step", "restart"]


@app.post("/replay/control")
async def replay_control(command: ReplayCommand):
    async with loop.control_lock:
        try:
            result = await grid_client.replay(command.action)
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=f"Replay control failed: {exc}") from exc
        if command.action == "restart":
            state.reset()
            loop._last_replay_index = None
        state.data_source = result
        state.latest_grid_state = await grid_client.get_state()
        await loop._broadcast_snapshot()
        return result


@app.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket):
    await state.connect(websocket)
    # Send an immediate snapshot so a freshly connected dashboard isn't
    # blank until the next tick.
    await websocket.send_json({"type": "state_update", "data": state.snapshot()})
    try:
        while True:
            # We don't expect the frontend to send anything meaningful,
            # but we need to await something to detect disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        state.disconnect(websocket)


# Serves orchestrator/static/index.html at "/" (html=True enables that
# fallback). Mounted LAST so it never shadows the API routes above —
# Starlette checks routes in registration order, and an exact-path
# route always wins before falling through to this catch-all mount.
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
