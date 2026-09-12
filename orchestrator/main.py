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

from contextlib import asynccontextmanager
from pathlib import Path

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
    await grid_client.reset()
    state.reset()
    state.log_event("System", "Scenario reset to normal state")
    return {"status": "reset"}


@app.post("/scenario/{name}")
async def trigger_scenario(name: str):
    scenario = SCENARIOS.get(name)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"Unknown scenario '{name}'")

    await grid_client.inject_fault(scenario["feeder_id"], scenario["fault_type"])
    state.log_event("System", f"Scenario triggered: {name}")
    return {"status": "triggered", "scenario": name}


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
