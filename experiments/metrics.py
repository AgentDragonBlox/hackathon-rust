"""
metrics.py

Pure functions that turn one mode's hour-by-hour simulation record (a
list[dict], one entry per replay hour -- see run_experiment.py for how
each mode builds these) into the summary numbers Task 4 asks for. Kept
free of pandapower/httpx/asyncio so they're fast and trivial to unit
test with synthetic data (see tests/test_experiment_metrics.py) --
run_experiment.py is the only place that has to actually run a
simulation to produce a `series`.

Each hour record is expected to have (see run_experiment.py's
_run_no_intervention / _run_load_shedding / _run_market_system for how
these are populated):

    hour_index: int
    timestamp: str
    feeders: dict[str, float]              -- loading_percent by feeder/transformer name
    scheduled_kw: dict[str, float]          -- per-asset kW the recorded/assumed profile called for this hour
    served_kw: dict[str, float]             -- per-asset kW actually delivered after any curtailment
    battery_soc_percent: float | None
    actions: list[dict]                    -- applied actions this hour (asset_id, action_type, kw_amount)
    proposed: int, accepted: int, rejected: int, settled: int
    decision_latency_ms: float | None       -- wall-clock time of the market round trip, if one ran this hour

Nothing here fabricates a number for a metric a mode didn't actually
exercise -- see action_counts()/decision_latency_stats() returning
explicit None/"n/a" for strategies that don't run a market at all.
"""

from __future__ import annotations

CRITICAL_ASSET_KEY = "hospital_load"
LOADING_LIMIT_PCT = 100.0


def overloaded_timesteps(series: list[dict], limit: float = LOADING_LIMIT_PCT) -> int:
    """Number of hours where at least one feeder/transformer exceeded `limit`."""
    return sum(1 for row in series if any(v > limit for v in row["feeders"].values()))


def peak_overload_pct(series: list[dict], limit: float = LOADING_LIMIT_PCT) -> float:
    """Largest amount by which any feeder/transformer exceeded `limit`,
    anywhere in the run. 0.0 if the limit was never exceeded."""
    peak = 0.0
    for row in series:
        for value in row["feeders"].values():
            peak = max(peak, value - limit)
    return round(peak, 2)


def critical_demand_served_pct(series: list[dict], critical_key: str = CRITICAL_ASSET_KEY) -> float:
    """Percent of the critical asset's scheduled energy actually served,
    summed across the run. 100.0 if it was never curtailed (or if the
    critical asset draws zero scheduled energy the whole run, to avoid a
    divide-by-zero implying failure where nothing was ever asked for)."""
    scheduled = sum(row["scheduled_kw"].get(critical_key, 0.0) for row in series)
    served = sum(row["served_kw"].get(critical_key, 0.0) for row in series)
    if scheduled <= 0:
        return 100.0
    return round(served / scheduled * 100.0, 3)


def unserved_energy_kwh(series: list[dict], sample_hours: float = 1.0) -> float:
    """Total energy (kWh, across every asset) not delivered relative to
    what the recorded/assumed profile called for -- whatever the cause
    (a deliberate curtailment action, or the network failing to serve a
    load at all). This is the honest "how much demand went unmet" number;
    see curtailed_energy_kwh for "how much was deliberately shed."""
    total = 0.0
    for row in series:
        for asset, scheduled_kw in row["scheduled_kw"].items():
            served_kw = row["served_kw"].get(asset, scheduled_kw)
            total += max(scheduled_kw - served_kw, 0.0) * sample_hours
    return round(total, 4)


def curtailed_energy_kwh(series: list[dict], sample_hours: float = 1.0) -> float:
    """Total energy (kWh) deliberately removed by an applied
    load-reduction-style action (load_reduction/hvac_reduction/ev_delay/
    production_flex) -- excludes battery_discharge (that's supplied, not
    curtailed) and excludes any unserved energy that wasn't the result of
    an explicit action (e.g. a non-convergent hour), so it can differ
    from unserved_energy_kwh in a run where those diverge."""
    CURTAILING_TYPES = {"load_reduction", "hvac_reduction", "ev_delay", "production_flex"}
    total = 0.0
    for row in series:
        for action in row.get("actions", []):
            if action["action_type"] in CURTAILING_TYPES:
                total += action["kw_amount"] * sample_hours
    return round(total, 4)


def final_battery_soc_percent(series: list[dict]) -> float | None:
    if not series:
        return None
    return series[-1].get("battery_soc_percent")


def action_counts(series: list[dict]) -> dict:
    return {
        "proposed": sum(row.get("proposed", 0) for row in series),
        "accepted": sum(row.get("accepted", 0) for row in series),
        "rejected": sum(row.get("rejected", 0) for row in series),
        "settled": sum(row.get("settled", 0) for row in series),
    }


def decision_latency_stats(series: list[dict]) -> dict:
    """Average/max wall-clock latency (ms) of the market round trip
    (offers + clear_market + settle), over hours where a market actually
    ran. Returns Nones (never a fabricated 0) when no market ever ran --
    e.g. the no-intervention and load-shedding baselines, which don't use
    a market at all."""
    samples = [row["decision_latency_ms"] for row in series if row.get("decision_latency_ms") is not None]
    if not samples:
        return {"avg_ms": None, "max_ms": None, "samples": 0}
    return {
        "avg_ms": round(sum(samples) / len(samples), 3),
        "max_ms": round(max(samples), 3),
        "samples": len(samples),
    }


def summarize(series: list[dict], critical_key: str = CRITICAL_ASSET_KEY) -> dict:
    """The single entry point run_experiment.py calls per mode -- bundles
    every metric above into the one dict that becomes a column of the
    results table."""
    return {
        "overloaded_timesteps": overloaded_timesteps(series),
        "peak_overload_pct": peak_overload_pct(series),
        "critical_demand_served_pct": critical_demand_served_pct(series, critical_key),
        "unserved_energy_kwh": unserved_energy_kwh(series),
        "curtailed_energy_kwh": curtailed_energy_kwh(series),
        "final_battery_soc_percent": final_battery_soc_percent(series),
        "actions": action_counts(series),
        "decision_latency": decision_latency_stats(series),
        "hours_simulated": len(series),
    }
