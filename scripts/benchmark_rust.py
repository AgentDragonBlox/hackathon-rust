"""
benchmark_rust.py

Task 9: measure the Rust market engine's actual latency instead of
asserting a performance claim. Launches the real agent_engines binary
(not a mock) as a subprocess and repeatedly calls its three HTTP routes
with a realistic payload (five assets, one flexibility request per
round -- the same shape orchestrator/loop.py sends), timing each route
and the full offers+clear_market+settle round trip.

This is NOT a Rust-vs-Python benchmark: there is no equivalent Python
reimplementation of the market/clearing/settlement logic in this
repository to compare against (see README section 8/9's own note that
"Rust is a substantive implementation choice ... The repository contains
no latency, throughput or Rust-versus-Python benchmark proving a
performance benefit"). This script only answers "how fast is the Rust
service, measured" -- not "is Rust faster than Python here."

Usage (from the project root, Rust binary already built via
`cargo build --locked`):

    python scripts/benchmark_rust.py [--iterations 200]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
RUST_BIN = ROOT / "target" / "debug" / "agent_engines"

GRID_STATE_PAYLOAD = {
    "timestamp": "2016-06-08T06:00:00Z",
    "assets": [
        {"asset_id": "hosp-1", "asset_type": "hospital", "current_load_kw": 380.0, "online": True},
        {"asset_id": "acad-1", "asset_type": "academic", "current_load_kw": 240.0, "online": True},
        {"asset_id": "ev-1", "asset_type": "ev", "current_load_kw": 0.0, "online": True},
        {"asset_id": "fac-1", "asset_type": "factory", "current_load_kw": 128.4, "online": True},
        {"asset_id": "batt-1", "asset_type": "battery", "current_load_kw": 0.0, "soc_percent": 72.0,
         "min_reserve_percent": 20.0, "online": True},
    ],
    "feeders": [{
        "feeder_id": "TRANSFORMER", "loading_percent": 84.3,
        "connected_assets": ["hosp-1", "acad-1", "ev-1", "fac-1", "batt-1"],
    }],
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_healthy(url: str, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=2) as client:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"Rust process exited early (code {proc.returncode})")
            try:
                if client.get(f"{url}/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
    raise RuntimeError(f"{url} did not become healthy within {timeout}s")


async def _timed_round_trip(client: httpx.AsyncClient, base_url: str) -> dict:
    request = {
        "request_id": str(uuid.uuid4()), "feeder_id": "TRANSFORMER", "kw_needed": 40.0,
        "deadline_seconds": 120.0, "reason": "benchmark",
    }

    t0 = time.perf_counter()
    offers_resp = await client.post(f"{base_url}/agents/offers", json={
        "grid_state": GRID_STATE_PAYLOAD, "requests": [request],
    })
    offers_resp.raise_for_status()
    offers = offers_resp.json()
    t1 = time.perf_counter()

    clear_resp = await client.post(f"{base_url}/agents/clear_market", json={"offers": offers})
    clear_resp.raise_for_status()
    actions = clear_resp.json()
    t2 = time.perf_counter()

    settle_resp = await client.post(f"{base_url}/agents/settle", json=actions)
    settle_resp.raise_for_status()
    t3 = time.perf_counter()

    return {
        "offers_ms": (t1 - t0) * 1000.0,
        "clear_market_ms": (t2 - t1) * 1000.0,
        "settle_ms": (t3 - t2) * 1000.0,
        "total_ms": (t3 - t0) * 1000.0,
        "n_offers": len(offers),
        "n_actions": len(actions),
    }


async def run_benchmark(base_url: str, iterations: int) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        samples = [await _timed_round_trip(client, base_url) for _ in range(iterations)]

    def _stats(key: str) -> dict:
        values = [s[key] for s in samples]
        values.sort()
        p50 = values[len(values) // 2]
        p95 = values[int(len(values) * 0.95)] if len(values) > 1 else values[0]
        return {
            "min_ms": round(min(values), 3), "mean_ms": round(statistics.mean(values), 3),
            "p50_ms": round(p50, 3), "p95_ms": round(p95, 3), "max_ms": round(max(values), 3),
        }

    return {
        "iterations": iterations,
        "offers": _stats("offers_ms"),
        "clear_market": _stats("clear_market_ms"),
        "settle": _stats("settle_ms"),
        "total_round_trip": _stats("total_ms"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=200)
    args = parser.parse_args()

    if not RUST_BIN.exists():
        raise SystemExit("Rust binary not built -- run `cargo build --locked` first.")

    port = _free_port()
    proc = subprocess.Popen(
        [str(RUST_BIN)], cwd=ROOT, env={**os.environ, "PORT": str(port)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        base_url = f"http://127.0.0.1:{port}"
        _wait_healthy(base_url, proc)
        print(f"Rust agent_engines healthy on {base_url}. Running {args.iterations} round trips...")
        results = asyncio.run(run_benchmark(base_url, args.iterations))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print(f"\nMeasured on this machine (localhost, {sys.platform}), {results['iterations']} sequential round trips:")
    print(f"{'Route':<20}{'min':>8}{'mean':>8}{'p50':>8}{'p95':>8}{'max':>8}  (ms)")
    for name, key in (("/agents/offers", "offers"), ("/agents/clear_market", "clear_market"),
                      ("/agents/settle", "settle"), ("full round trip", "total_round_trip")):
        s = results[key]
        print(f"{name:<20}{s['min_ms']:>8.2f}{s['mean_ms']:>8.2f}{s['p50_ms']:>8.2f}{s['p95_ms']:>8.2f}{s['max_ms']:>8.2f}")
    print(
        "\nNo Python reimplementation of this market exists in this repository, so this is NOT a "
        "Rust-vs-Python comparison -- it is a measured latency figure for the Rust service alone."
    )


if __name__ == "__main__":
    main()
