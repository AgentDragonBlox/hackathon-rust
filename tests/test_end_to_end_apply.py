"""
Milestone 7/8 end-to-end test: runs the REAL Rust market engine binary
and a REAL grid_engine.api Python service -- actual separate server
processes, not mocks or fakes -- through one full orchestrator tick, and
confirms a settled action changes what the NEXT GET /grid/state reports.

This is the exact gap flagged throughout README.md (sections 4, 8, 9, 18,
21, 22: "Settlement does not commit actions into the replay network,
update battery SOC through dispatch, or control equipment") -- closed by
grid_engine/api.py's POST /grid/apply and orchestrator/loop.py's call to
it after a successful settlement. If this test passes, the full pipeline
OBSERVE -> offers (Rust) -> clear_market (Rust) -> validate (Python) ->
settle (Rust) -> apply (Python) -> next GET /grid/state is proven to work
against real processes, not just unit-level fakes.

Skipped automatically if the Rust binary hasn't been built yet (this is
an integration check, not a pure-Python unit test, and shouldn't fail a
Python-only test run in an environment without a Rust toolchain).
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

import orchestrator.loop as loop_module
from orchestrator.clients.agent_client import AgentClient
from orchestrator.clients.grid_client import GridClient
from orchestrator.loop import OrchestrationLoop
from orchestrator.state import SystemState
from shared.contracts import Prediction

ROOT = Path(__file__).resolve().parents[1]
RUST_BIN = ROOT / "target" / "debug" / "agent_engines"

pytestmark = pytest.mark.skipif(
    not RUST_BIN.exists(), reason="Rust binary not built -- run `cargo build --locked` first"
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_healthy(url: str, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=2) as client:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"process for {url} exited early (code {proc.returncode})")
            try:
                if client.get(f"{url}/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
    raise RuntimeError(f"{url} did not become healthy within {timeout}s")


@pytest.fixture(scope="module")
def live_services():
    rust_port = _free_port()
    grid_port = _free_port()

    rust_proc = subprocess.Popen(
        [str(RUST_BIN)], cwd=ROOT,
        env={**os.environ, "PORT": str(rust_port)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    grid_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "grid_engine.api:app", "--port", str(grid_port)],
        cwd=ROOT, env={**os.environ, "GRID_DATA_MODE": "baseline"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_healthy(f"http://127.0.0.1:{rust_port}", rust_proc)
        _wait_healthy(f"http://127.0.0.1:{grid_port}", grid_proc)
        yield f"http://127.0.0.1:{rust_port}", f"http://127.0.0.1:{grid_port}"
    finally:
        for proc in (rust_proc, grid_proc):
            proc.terminate()
        for proc in (rust_proc, grid_proc):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def test_full_pipeline_settled_action_changes_next_grid_state(live_services, monkeypatch):
    rust_url, grid_url = live_services
    grid_client = GridClient(base_url=grid_url)
    agent_client = AgentClient(base_url=rust_url)

    demo_state = SystemState()
    monkeypatch.setattr(loop_module, "state", demo_state)
    engine = OrchestrationLoop(grid_client, agent_client)

    # Real grid state, straight from the real Python AC power-flow model
    # (baseline network: F1=71.0% loaded, hosp-1=380kW, batt-1 SOC=72% --
    # see test_scenarios.py's documented baseline).
    grid_state_before = asyncio.run(grid_client.get_state())
    assets_before = {a.asset_id: a for a in grid_state_before.assets}

    # F1 isn't actually over 100% at baseline -- feed the loop a real
    # Prediction object the same shape loop._derive_predictions() would
    # produce once loading crosses the 80% threshold, so this test
    # exercises the market/settle/apply path without needing a fault
    # search just to get into the method under test.
    prediction = Prediction(feeder_id="F1", predicted_overload=True, eta_seconds=120)
    asyncio.run(engine._run_market_for_feeder(grid_state_before, prediction))

    error_events = [e.message for e in demo_state.event_log if e.severity == "error"]
    assert demo_state.trades, f"no trades settled -- events: {[e.message for e in demo_state.event_log]}"
    assert not any("NOT applied" in msg for msg in error_events), error_events

    grid_state_after = asyncio.run(grid_client.get_state())
    assets_after = {a.asset_id: a for a in grid_state_after.assets}

    changed = any(
        (
            assets_after[asset_id].current_load_kw != before.current_load_kw
            or assets_after[asset_id].current_gen_kw != before.current_gen_kw
            or assets_after[asset_id].soc_percent != before.soc_percent
        )
        for asset_id, before in assets_before.items()
    )
    assert changed, (
        "settled trade did not change the next /grid/state response -- "
        "the physical feedback loop is not actually closed"
    )
