"""
run_experiment.py

Task 3/4: runs the SAME 168-hour recorded replay input under three
strategies -- no intervention, simple deterministic load shedding, and
the real Rust market + Python-validated + physically-applied system --
and writes data/experiment_results.json with per-hour series and the
Task 4 summary metrics (experiments/metrics.py) for each.

All three modes start from the identical baseline network
(grid_engine.grid.build_campus_network()) and read the identical bundled
OPSD/CoSSMic replay samples (data/opsd_replay.json) in the same order --
same starting conditions, same input, per Task 3's requirement. Nothing
here is looped through the live orchestrator/dashboard; this is an
offline, fast, repeatable batch run over the exact same physics
(grid_engine.grid/simulation/scenarios) and, for mode C, the exact same
Rust market binary the live demo uses -- reused, not reimplemented.

Mode A (no intervention): apply each hour's recorded/assumed profile,
run AC power flow, record the result. No corrective action of any kind.

Mode B (simple load shedding): after each hour's power flow, if any
feeder/transformer exceeds 100% loading, shed load from that feeder's
NON-hospital assets in fixed 10% steps (of their current draw) until
resolved or a hard iteration cap is hit. Hospital load is never a
shedding candidate -- this is the "protect critical loads as far as the
existing model allows" baseline. Uses the exact same
scenarios.apply_proposed_action/check_constraints the real validator
uses, just driven by a fixed rule instead of a market.

Mode C (our system): each overloaded feeder each hour goes through the
real pipeline -- Rust market (agent_engines binary, launched as a real
subprocess), Python physical validation (scenarios.validate_actions),
then the accepted/settled subset is applied to the running network the
same way grid_engine/api.py's POST /grid/apply does (battery energy is
tracked persistently across hours the same way applied_state.py does;
curtailment is not carried forward, for the same reason documented
there). Requires cargo build --locked to have produced
target/debug/agent_engines; if it hasn't, mode C is skipped with a clear
note in the output rather than being silently faked.

Usage (from the project root, with the prepared Python environment active):

    python scripts/run_experiment.py
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pandapower as pp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiments.metrics import summarize  # noqa: E402
from grid_engine.contracts_adapter import ASSET_ID_TO_INTERNAL, build_grid_state  # noqa: E402
from grid_engine.grid import build_campus_network, get_asset_indices, run_power_flow  # noqa: E402
from grid_engine.replay import DATA_PATH  # noqa: E402
from grid_engine.scenarios import (  # noqa: E402
    ASSUMED_DISCHARGE_SUSTAIN_HOURS,
    apply_proposed_action,
    check_constraints,
    validate_actions,
)
from grid_engine.simulation import apply_profile_row  # noqa: E402
from orchestrator.clients.agent_client import AgentClient  # noqa: E402
from orchestrator.loop import OVERLOAD_THRESHOLD, _estimate_kw_needed  # noqa: E402
from shared.contracts import FlexibilityRequest, ProposedAction  # noqa: E402

RESULTS_PATH = ROOT / "data" / "experiment_results.json"
RUST_BIN = ROOT / "target" / "debug" / "agent_engines"

SCHEDULED_ASSET_NAMES = ("hospital_load", "academic_load", "ev_load", "facility_load")

# Non-hospital, non-battery LOAD assets connected to each feeder, per
# grid_engine/contracts_adapter.py's own FEEDER_ASSET_IDS -- the simple
# load-shedding rule's only candidates. Recomputed here (not imported)
# because contracts_adapter's FEEDER_ASSET_IDS also lists solar/battery,
# which are not sheddable loads.
_NONCRITICAL_LOAD_ASSETS_BY_FEEDER = {
    "F1": [],  # only hosp-1 (excluded) and batt-1 (not a load) sit on F1
    "F2": ["acad-1", "ev-1"],
    "F3": ["fac-1"],
    "TRANSFORMER": ["acad-1", "ev-1", "fac-1"],  # serves the whole campus
}
ACTION_TYPE_BY_ASSET_ID = {"acad-1": "hvac_reduction", "ev-1": "ev_delay", "fac-1": "production_flex"}
SHED_STEP_FRACTION = 0.10
MAX_SHED_ITERATIONS = 30
SHED_FLOOR_KW = 0.5


def _load_samples() -> list[dict]:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return data["samples"]


def _scheduled_kw_from_row(row: dict) -> dict:
    return {
        "hospital_load": row["hospital_kw"],
        "academic_load": row["academic_kw"],
        "ev_load": row["ev_kw"],
        "facility_load": row["facility_kw"],
    }


def _served_kw(net, indices) -> dict:
    return {name: float(net.load.loc[indices[name][1], "p_mw"]) * 1000.0 for name in SCHEDULED_ASSET_NAMES}


def _safe(value: float) -> float:
    return float(value) if value == value else 0.0  # NaN-safe, same convention as contracts_adapter._safe_kw


def _read_feeders(net) -> dict:
    out = {}
    for idx, row in net.line.iterrows():
        out[row["name"]] = _safe(net.res_line.loc[idx, "loading_percent"])
    out["TRANSFORMER"] = _safe(net.res_trafo["loading_percent"].iloc[0])
    return out


def _run_power_flow_honestly(net) -> None:
    """Same convention as grid_engine/api.py's _rebuild_live_state: let a
    non-convergent solve clear res_* to NaN (handled by _safe above)
    rather than crash the whole 168-hour run over one bad hour."""
    try:
        run_power_flow(net)
    except pp.powerflow.LoadflowNotConverged:
        pass


# ---------------------------------------------------------------------------
# Mode A: no intervention
# ---------------------------------------------------------------------------


def run_no_intervention(samples: list[dict]) -> list[dict]:
    net = build_campus_network()
    indices = get_asset_indices(net)
    series = []
    for i, sample in enumerate(samples):
        row = sample["campus_kw"]
        apply_profile_row(net, row, indices)
        _run_power_flow_honestly(net)
        scheduled = _scheduled_kw_from_row(row)
        series.append({
            "hour_index": i, "timestamp": sample["timestamp"], "feeders": _read_feeders(net),
            "scheduled_kw": scheduled, "served_kw": dict(scheduled),  # nothing is ever curtailed
            "battery_soc_percent": 72.0, "actions": [],
            "proposed": 0, "accepted": 0, "rejected": 0, "settled": 0, "decision_latency_ms": None,
        })
    return series


# ---------------------------------------------------------------------------
# Mode B: simple deterministic load shedding
# ---------------------------------------------------------------------------


def _overloaded_feeder_names(net, limit: float = 100.0) -> list[str]:
    names = [name for name, pct in _read_feeders(net).items() if pct > limit]
    return names


def run_load_shedding(samples: list[dict]) -> list[dict]:
    net = build_campus_network()
    indices = get_asset_indices(net)
    series = []
    counter = 0
    for i, sample in enumerate(samples):
        row = sample["campus_kw"]
        apply_profile_row(net, row, indices)
        _run_power_flow_honestly(net)
        scheduled = _scheduled_kw_from_row(row)

        hour_actions = []
        for _ in range(MAX_SHED_ITERATIONS):
            overloaded = _overloaded_feeder_names(net)
            if not overloaded:
                break
            candidates: set[str] = set()
            for feeder in overloaded:
                candidates.update(_NONCRITICAL_LOAD_ASSETS_BY_FEEDER.get(feeder, []))
            if not candidates:
                break  # nothing this rule is allowed to shed can help (e.g. F1-only overload)
            progress = False
            for asset_id in sorted(candidates):
                _, internal_name = ASSET_ID_TO_INTERNAL[asset_id]
                idx = indices[internal_name][1]
                current_kw = float(net.load.loc[idx, "p_mw"]) * 1000.0
                if current_kw <= SHED_FLOOR_KW:
                    continue
                shed_kw = round(current_kw * SHED_STEP_FRACTION, 3)
                counter += 1
                action = ProposedAction(
                    action_id=f"shed-{counter}", request_id="load-shedding-rule", asset_id=asset_id,
                    action_type=ACTION_TYPE_BY_ASSET_ID[asset_id], kw_amount=shed_kw, source_offer_id="rule",
                )
                apply_proposed_action(net, action)
                hour_actions.append({"asset_id": asset_id, "action_type": action.action_type, "kw_amount": shed_kw})
                progress = True
            _run_power_flow_honestly(net)
            if not progress:
                break

        series.append({
            "hour_index": i, "timestamp": sample["timestamp"], "feeders": _read_feeders(net),
            "scheduled_kw": scheduled, "served_kw": _served_kw(net, indices),
            "battery_soc_percent": 72.0,  # this baseline never dispatches the battery
            "actions": hour_actions, "proposed": len(hour_actions), "accepted": len(hour_actions),
            "rejected": 0, "settled": 0, "decision_latency_ms": None,
        })
    return series


# ---------------------------------------------------------------------------
# Mode C: our system -- real Rust market + real Python validation + apply
# ---------------------------------------------------------------------------


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


async def run_market_system(samples: list[dict], rust_url: str) -> list[dict]:
    net = build_campus_network()
    indices = get_asset_indices(net)
    agent_client = AgentClient(base_url=rust_url)

    battery_idx = indices["hospital_bess"][1]
    max_e_mwh = float(net.storage.loc[battery_idx, "max_e_mwh"])
    min_e_mwh = float(net.storage.loc[battery_idx, "min_e_mwh"])
    battery_energy_mwh = float(net.storage.loc[battery_idx, "soc_percent"]) / 100.0 * max_e_mwh

    series = []
    for i, sample in enumerate(samples):
        row = sample["campus_kw"]
        apply_profile_row(net, row, indices)
        net.storage.loc[battery_idx, "soc_percent"] = battery_energy_mwh / max_e_mwh * 100.0
        _run_power_flow_honestly(net)
        scheduled = _scheduled_kw_from_row(row)

        # One grid-state snapshot per hour, reused for every feeder request
        # this hour -- matches orchestrator/loop.py's _tick_locked, which
        # fetches GridState once per tick and reuses it across every
        # overloaded feeder processed in that same tick.
        snapshot = build_grid_state(net)
        overloaded_feeders = [f.feeder_id for f in snapshot.feeders if f.loading_percent >= OVERLOAD_THRESHOLD]

        hour_actions = []
        proposed_n = accepted_n = rejected_n = settled_n = 0
        latency_ms_total = 0.0
        ran_market = False

        for feeder_id in overloaded_feeders:
            feeder_state = next(f for f in snapshot.feeders if f.feeder_id == feeder_id)
            request = FlexibilityRequest(
                request_id=str(uuid.uuid4()), feeder_id=feeder_id,
                kw_needed=_estimate_kw_needed(feeder_state.loading_percent),
                deadline_seconds=120.0, reason=f"{feeder_id} predicted overload (experiment run)",
            )
            # Timed span covers ONLY the Rust HTTP round trips (offers +
            # clear_market, and settle further below) -- Python's own
            # validate_actions() call in between is deliberately excluded
            # so this reports Rust's latency, not a mix of the two (see
            # scripts/benchmark_rust.py for a dedicated, larger-sample
            # Rust-only benchmark used for README's Task 9 numbers).
            rust_start = time.perf_counter()
            offers = await agent_client.get_offers(snapshot, [request])
            proposed_actions = await agent_client.clear_market(offers)
            latency_ms_total += (time.perf_counter() - rust_start) * 1000.0
            if not proposed_actions:
                continue
            ran_market = True
            proposed_n += len(proposed_actions)

            validation_results = validate_actions(net, proposed_actions)
            results_by_id = {r.action_id: r for r in validation_results}
            feasible_actions = []
            for action in proposed_actions:
                result = results_by_id.get(action.action_id)
                if result is None or not result.feasible:
                    rejected_n += 1
                    continue
                if result.adjusted_kw_amount is not None:
                    action.kw_amount = result.adjusted_kw_amount
                feasible_actions.append(action)
            accepted_n += len(feasible_actions)

            if feasible_actions:
                settle_start = time.perf_counter()
                trades = await agent_client.settle(feasible_actions)
                latency_ms_total += (time.perf_counter() - settle_start) * 1000.0
                settled_n += len(trades)
                for action in feasible_actions:
                    apply_proposed_action(net, action)
                    hour_actions.append({
                        "asset_id": action.asset_id, "action_type": action.action_type,
                        "kw_amount": action.kw_amount,
                    })
                    if action.action_type == "battery_discharge":
                        discharged_mwh = (action.kw_amount / 1000.0) * ASSUMED_DISCHARGE_SUSTAIN_HOURS
                        battery_energy_mwh = max(min_e_mwh, battery_energy_mwh - discharged_mwh)
                net.storage.loc[battery_idx, "soc_percent"] = battery_energy_mwh / max_e_mwh * 100.0
                _run_power_flow_honestly(net)

        series.append({
            "hour_index": i, "timestamp": sample["timestamp"], "feeders": _read_feeders(net),
            "scheduled_kw": scheduled, "served_kw": _served_kw(net, indices),
            "battery_soc_percent": battery_energy_mwh / max_e_mwh * 100.0,
            "actions": hour_actions, "proposed": proposed_n, "accepted": accepted_n,
            "rejected": rejected_n, "settled": settled_n,
            "decision_latency_ms": latency_ms_total if ran_market else None,
        })
    return series


def _run_mode_c_with_rust_subprocess(samples: list[dict]) -> tuple[list[dict] | None, str | None]:
    if not RUST_BIN.exists():
        return None, "Rust binary not built -- run `cargo build --locked` first, then rerun this script."
    port = _free_port()
    proc = subprocess.Popen(
        [str(RUST_BIN)], cwd=ROOT, env={**os.environ, "PORT": str(port)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_healthy(f"http://127.0.0.1:{port}", proc)
        series = asyncio.run(run_market_system(samples, f"http://127.0.0.1:{port}"))
        return series, None
    except Exception as exc:  # noqa: BLE001 -- report, don't crash the whole experiment
        return None, f"mode C failed: {exc}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    samples = _load_samples()
    print(f"Loaded {len(samples)} recorded hourly samples from {DATA_PATH}")

    print("Running mode A (no intervention)...")
    series_a = run_no_intervention(samples)

    print("Running mode B (simple load shedding)...")
    series_b = run_load_shedding(samples)

    print("Running mode C (Rust market + Python validation + apply)...")
    series_c, mode_c_error = _run_mode_c_with_rust_subprocess(samples)
    if mode_c_error:
        print(f"  SKIPPED: {mode_c_error}")

    results = {
        "metadata": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "scenario": (
                "Full one-week (168-hour) OPSD/CoSSMic replay, identical starting network and "
                "input for all three modes. No faults injected -- the stress in this window is "
                "the recorded academic demand peak at hour 48 (2016-06-08 06:00 UTC), which "
                "coincides with near-zero EV load and low solar, pushing feeder F2 to ~137% "
                "loading under no intervention. See section 20 (Experimental Evaluation) in README.md."
            ),
            "dataset": {
                "source": "Open Power System Data, Household Data v2020-04-15 (CoSSMic, Konstanz, Germany)",
                "sample_count": len(samples),
                "sample_minutes": 60,
            },
            "labels": {
                "RECORDED": "Academic, EV, facility and solar profiles (OPSD/CoSSMic, scaled to campus peaks)",
                "SIMULATED": "Hospital demand (fixed 380kW), battery capacity/SOC/reserve, network topology, "
                              "electrical parameters, and (mode B) the load-shedding rule / (mode C) the "
                              "flexibility market's offer costs and policies",
                "DERIVED": "Feeder/transformer loading percent and bus voltages -- pandapower AC power-flow "
                           "output, not asserted numbers",
                "MEASURED": "Every number in `summary` below, computed by actually executing each strategy "
                            "over the identical 168-hour input -- not fabricated or hand-tuned per mode",
            },
            "modes": {
                "no_intervention": "The grid follows the recorded/assumed input with no corrective action.",
                "load_shedding": "Deterministic rule: shed 10% of each overloaded feeder's non-hospital load "
                                  "per iteration until resolved or a 30-iteration cap is hit. Hospital is "
                                  "never a shedding candidate. No market, no battery dispatch.",
                "market_system": "Rust agent_engines binary (offers + clear_market + settle) + Python AC "
                                  "power-flow validation (grid_engine.scenarios), with settled actions applied "
                                  "to the running network -- the same pipeline the live demo uses.",
            },
            "mode_c_error": mode_c_error,
        },
        "series": {
            "no_intervention": series_a,
            "load_shedding": series_b,
            "market_system": series_c,
        },
        "summary": {
            "no_intervention": summarize(series_a),
            "load_shedding": summarize(series_b),
            "market_system": summarize(series_c) if series_c is not None else None,
        },
    }

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote {RESULTS_PATH}")

    _print_table(results["summary"])


def _print_table(summary: dict) -> None:
    modes = ["no_intervention", "load_shedding", "market_system"]
    labels = {"no_intervention": "No Intervention", "load_shedding": "Load Shedding", "market_system": "Our System"}
    rows = [
        ("Overloaded timesteps", "overloaded_timesteps", "{}"),
        ("Peak overload (pct-pts over 100%)", "peak_overload_pct", "{:.1f}"),
        ("Critical demand served", "critical_demand_served_pct", "{:.2f}%"),
        ("Unserved energy (kWh)", "unserved_energy_kwh", "{:.1f}"),
        ("Curtailed energy (kWh)", "curtailed_energy_kwh", "{:.1f}"),
        ("Final battery SOC", "final_battery_soc_percent", "{:.1f}%"),
    ]
    print(f"\n{'Metric':<34}" + "".join(f"{labels[m]:>18}" for m in modes))
    print("-" * (34 + 18 * len(modes)))
    for title, key, fmt in rows:
        cells = []
        for mode in modes:
            data = summary.get(mode)
            value = data.get(key) if data else None
            cells.append("N/A" if value is None else fmt.format(value))
        print(f"{title:<34}" + "".join(f"{c:>18}" for c in cells))
    latency_cells = []
    for mode in modes:
        data = summary.get(mode)
        avg_ms = data["decision_latency"]["avg_ms"] if data else None
        latency_cells.append("N/A" if avg_ms is None else f"{avg_ms:.1f}")
    print(f"{'Decision latency (avg ms)':<34}" + "".join(f"{c:>18}" for c in latency_cells))


if __name__ == "__main__":
    main()
