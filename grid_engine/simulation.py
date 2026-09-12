"""
simulation.py

Milestone 2: synthetic time-series load/solar profiles, and a driver that
steps the Milestone-1 network (grid.py) through them minute-by-minute,
rerunning AC power flow at each step and recording the results.

This gives us a believable, physically *computed* (not fabricated) time
series that Milestone 3 (forecasting.py) can extrapolate from, and that
Milestone 4 (violation detection) can scan for constraint crossings.

-------------------------------------------------------------------------
PROFILE DESIGN (synthetic, clearly labeled -- not measured data)
-------------------------------------------------------------------------

Default horizon: 180 minutes (3 hours) at 1-minute resolution -- long
enough to show a believable "F2 overload in ~15 minutes" style forecast
window, short/coarse enough to stay fast and easy to debug.

Each asset gets a simple, deterministic (seeded) synthetic pattern rather
than a black-box ML-generated one, per the project's prediction
philosophy of not pretending to have data/training we don't have:

- hospital: ~flat at its Milestone-1 baseline (380 kW) with small slow
  drift. Critical load -- deliberately not varied much.
- facility: ~flat at baseline (150 kW) with small slow drift.
- academic: ~flat at its Milestone-1 baseline (240 kW), small noise only.
- ev: ~flat at its Milestone-1 baseline (70 kW), small noise only.
- solar: ~flat around the Milestone-1 baseline (165 kW) with gentle
  natural variation, plus a smooth (shallow, 15%) Gaussian dip around
  t=100-130 min representing a passing cloud -- ordinary solar
  intermittency, not the formal simulated "solar drop event" API, which
  is Milestone 6 scope for arbitrary operator-triggered shocks on top of
  any baseline.

Solar sits on feeder F2 (academic bus) alongside the academic and EV
loads, so a solar dip shows up as extra net import on F2. That's the
single, deliberate stress driver in this profile set -- see the note
inside generate_profiles() for why academic/EV are NOT also ramped: F2
turned out to have very little headroom above its own Milestone-1
baseline (~93% already), so this is a genuine, physically-motivated
finding, not a narrative choice. The actual loading numbers are computed
by power flow, not asserted.

All profiles include small seeded Gaussian noise so the time series
doesn't look like a hand-drawn piecewise function.
"""

import numpy as np
import pandas as pd
import pandapower as pp

from grid_engine.grid import (
    PF_BUILDING_LOAD,
    PF_EV_CHARGING,
    kw_to_mw,
    q_mvar_from_pf,
    get_asset_indices,
    run_power_flow,
)


def generate_profiles(horizon_min: int = 180, dt_min: int = 1, seed: int = 42) -> pd.DataFrame:
    """
    Generate synthetic per-minute load/solar profiles (in kW) for the
    campus assets. Returns a DataFrame indexed by "minute" (0..horizon_min)
    with columns: hospital_kw, academic_kw, ev_kw, facility_kw, solar_kw.

    Deterministic given `seed` -- important so demo runs are reproducible.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(0, horizon_min + dt_min, dt_min)

    # --- Hospital: flat critical load, tiny slow drift, small noise --------
    hospital_kw = 380.0 + 8.0 * np.sin(2 * np.pi * t / 60.0) + rng.normal(0, 2.0, size=t.size)

    # --- Facility: flat office load, tiny slow drift, small noise ----------
    facility_kw = 150.0 + 5.0 * np.sin(2 * np.pi * t / 45.0) + rng.normal(0, 2.0, size=t.size)

    # --- Academic: flat at the Milestone-1 baseline (240 kW), small noise --
    academic_kw = 240.0 + rng.normal(0, 1.5, size=t.size)

    # --- EV: flat at the Milestone-1 baseline (70 kW), small noise ---------
    ev_kw = 70.0 + rng.normal(0, 1.0, size=t.size)

    # --- Solar: ~flat around the Milestone-1 baseline (165 kW) with gentle
    #     natural variation, plus a Gaussian cloud dip at t~100-130 --------
    #
    # NOTE (finding from tuning this profile): feeder F2 (academic) turned
    # out to have very little headroom above its Milestone-1 baseline
    # loading of ~93% -- power flow shows it crosses 100% once solar drops
    # by only ~15 kW (~9%) below the 165 kW baseline. A real demand ramp on
    # academic/EV big enough to be interesting (tens of kW) would blow
    # straight through that headroom and stay in violation for the rest of
    # the run, which doesn't give a bounded, demo-friendly "predicted
    # violation" window. So academic/EV are left flat here, and the single
    # deliberate stress driver is a *shallow* cloud dip (15%, not a drastic
    # drop) -- enough to push F2 over 100% for a bounded stretch without
    # making the whole second half of the run one long violation. This is
    # itself a real finding worth carrying into the team's demo narrative:
    # F2 is a tight, marginal feeder with very little slack, which is
    # exactly the kind of feeder a flexibility market is most useful for.
    clear_sky_kw = 165.0 + 10.0 * np.sin(2 * np.pi * t / 70.0)
    cloud_factor = 1.0 - 0.15 * np.exp(-0.5 * ((t - 115) / 8.0) ** 2)
    solar_kw = clear_sky_kw * cloud_factor + rng.normal(0, 1.0, size=t.size)
    solar_kw = np.clip(solar_kw, 0, None)  # can't generate negative power

    # Loads shouldn't go negative either, given noise around a positive base.
    hospital_kw = np.clip(hospital_kw, 0, None)
    facility_kw = np.clip(facility_kw, 0, None)
    academic_kw = np.clip(academic_kw, 0, None)
    ev_kw = np.clip(ev_kw, 0, None)

    return pd.DataFrame(
        {
            "hospital_kw": hospital_kw,
            "academic_kw": academic_kw,
            "ev_kw": ev_kw,
            "facility_kw": facility_kw,
            "solar_kw": solar_kw,
        },
        index=pd.Index(t, name="minute"),
    )


def apply_profile_row(net: pp.pandapowerNet, row: pd.Series, indices: dict) -> None:
    """
    Push one profile row's kW values into the network's load/sgen elements,
    recomputing reactive power from the same power-factor assumptions used
    in grid.py's baseline build. Mutates `net` in place.
    """
    table, idx = indices["hospital_load"]
    p_mw = kw_to_mw(row["hospital_kw"])
    getattr(net, table).loc[idx, "p_mw"] = p_mw
    getattr(net, table).loc[idx, "q_mvar"] = q_mvar_from_pf(p_mw, PF_BUILDING_LOAD)

    table, idx = indices["academic_load"]
    p_mw = kw_to_mw(row["academic_kw"])
    getattr(net, table).loc[idx, "p_mw"] = p_mw
    getattr(net, table).loc[idx, "q_mvar"] = q_mvar_from_pf(p_mw, PF_BUILDING_LOAD)

    table, idx = indices["ev_load"]
    p_mw = kw_to_mw(row["ev_kw"])
    getattr(net, table).loc[idx, "p_mw"] = p_mw
    getattr(net, table).loc[idx, "q_mvar"] = q_mvar_from_pf(p_mw, PF_EV_CHARGING)

    table, idx = indices["facility_load"]
    p_mw = kw_to_mw(row["facility_kw"])
    getattr(net, table).loc[idx, "p_mw"] = p_mw
    getattr(net, table).loc[idx, "q_mvar"] = q_mvar_from_pf(p_mw, PF_BUILDING_LOAD)

    table, idx = indices["solar"]
    getattr(net, table).loc[idx, "p_mw"] = kw_to_mw(row["solar_kw"])
    # q_mvar for solar stays 0 (PF_SOLAR=1.0 assumption, set once at build time)


def run_time_series(net: pp.pandapowerNet, profiles: pd.DataFrame) -> pd.DataFrame:
    """
    Step `net` through each row of `profiles` (in minute order), rerunning
    AC power flow at every step, and return a results DataFrame indexed by
    minute with feeder/transformer loading and bus voltages.

    Mutates `net` in place as time progresses -- this represents "the"
    evolving network state. If you need to branch off a hypothetical
    future/alternative without disturbing this run, operate on a copy
    (see grid.py's build_campus_network(), and scenarios.py in a later
    milestone for applying proposed actions to a copy).

    Per-step convergence handling is DELIBERATELY different from
    run_power_flow()'s single-snapshot behavior: grid.py's run_power_flow
    does not swallow a LoadflowNotConverged, because for a single check
    that's an important signal to surface immediately. Here, across ~180
    steps, one bad/ill-conditioned minute shouldn't kill the whole run --
    so we catch non-convergence per step, record converged=False with NaN
    results for that minute, and continue. This is a genuine behavioral
    difference, not an accident -- don't copy this catch-and-continue
    pattern into run_power_flow() itself.
    """
    indices = get_asset_indices(net)
    records = []

    for minute, row in profiles.iterrows():
        apply_profile_row(net, row, indices)
        try:
            run_power_flow(net)
            converged = True
        except pp.powerflow.LoadflowNotConverged:
            converged = False

        if converged:
            f_loading = dict(zip(net.line["name"], net.res_line["loading_percent"]))
            trafo_loading = net.res_trafo["loading_percent"].iloc[0]
            vm_by_bus = dict(zip(net.bus["name"], net.res_bus["vm_pu"]))
            records.append({
                "minute": minute,
                "converged": True,
                "F1_loading_pct": f_loading.get("F1", np.nan),
                "F2_loading_pct": f_loading.get("F2", np.nan),
                "F3_loading_pct": f_loading.get("F3", np.nan),
                "trafo_loading_pct": trafo_loading,
                "hospital_vm_pu": vm_by_bus.get("hospital", np.nan),
                "academic_vm_pu": vm_by_bus.get("academic", np.nan),
                "facility_vm_pu": vm_by_bus.get("facility", np.nan),
            })
        else:
            records.append({
                "minute": minute,
                "converged": False,
                "F1_loading_pct": np.nan,
                "F2_loading_pct": np.nan,
                "F3_loading_pct": np.nan,
                "trafo_loading_pct": np.nan,
                "hospital_vm_pu": np.nan,
                "academic_vm_pu": np.nan,
                "facility_vm_pu": np.nan,
            })

    return pd.DataFrame(records).set_index("minute")


def summarize_time_series(results: pd.DataFrame) -> None:
    """Print a compact summary of a run_time_series() results DataFrame."""
    n_steps = len(results)
    n_nonconverged = (~results["converged"]).sum()
    print("=" * 60)
    print(f"TIME SERIES SUMMARY ({n_steps} steps)")
    print("=" * 60)
    if n_nonconverged:
        print(f"WARNING: {n_nonconverged} step(s) did not converge.")

    for col in ["F1_loading_pct", "F2_loading_pct", "F3_loading_pct", "trafo_loading_pct"]:
        series = results[col].dropna()
        if series.empty:
            continue
        peak_minute = series.idxmax()
        print(f"{col:20s} min={series.min():6.1f}%  max={series.max():6.1f}% (at t={peak_minute} min)")

    for col in ["hospital_vm_pu", "academic_vm_pu", "facility_vm_pu"]:
        series = results[col].dropna()
        if series.empty:
            continue
        print(f"{col:20s} min={series.min():.4f} pu  max={series.max():.4f} pu")

    # Report the first minute (if any) where any tracked feeder crosses 100%.
    over_limit = results[
        (results["F1_loading_pct"] > 100)
        | (results["F2_loading_pct"] > 100)
        | (results["F3_loading_pct"] > 100)
    ]
    if not over_limit.empty:
        first_violation_minute = over_limit.index[0]
        print()
        print(f"First feeder loading > 100% at t={first_violation_minute} min "
              f"(see forecasting.py in Milestone 3 for turning this into an "
              f"advance prediction).")
    else:
        print()
        print("No feeder exceeded 100% loading in this window.")


if __name__ == "__main__":
    from grid_engine.grid import build_campus_network

    net = build_campus_network()
    profiles = generate_profiles()
    results = run_time_series(net, profiles)
    summarize_time_series(results)
