"""
forecasting.py

Milestone 3: future-state forecasting. Fits a simple trend to recent
per-asset demand/generation history, projects it forward, builds
hypothetical *future* grid states from that projection, and reruns AC
power flow on them -- so "predicted congestion" is always a computed
result of a forecasted electrical state, never an asserted number.

-------------------------------------------------------------------------
PREDICTION PHILOSOPHY (this is the actual method used here, not a stand-in)
-------------------------------------------------------------------------

Per the project brief, we deliberately do NOT pretend to have a trained
ML model predicting "grid failure probability". Instead:

    1. Forecast future demand/generation (forecast_linear_trend below).
    2. Insert those forecasts into future grid states (forecast_grid_states).
    3. Run power flow on the predicted states (reuses simulation.py's
       run_time_series -- same power-flow code as the "real" time series,
       just fed forecasted inputs instead of measured ones).
    4. Read off predicted constraint violations from the results
       (summarize_forecast, and forecasting_report for the structured
       version other modules can consume).

The forecasting method itself is intentionally simple and transparent:
ordinary least-squares linear regression (numpy.polyfit, degree 1) of
each asset's kW value against time, fit over a short trailing window of
recent "measured" history, then extrapolated forward. This is defensible
precisely because it's simple -- there's no claim of a validated model,
just "if the recent trend continues, here's what the state would be, and
here's what that does to the network." Good enough to catch a feeder
riding a real trend toward its limit; not meant to handle sudden step
changes (a fault, an outage) -- those are Milestone 6's simulated events,
which forecasting doesn't try to anticipate.

-------------------------------------------------------------------------
WHERE "history" COMES FROM
-------------------------------------------------------------------------

In this milestone, "history" is a slice of the same kind of per-asset kW
profile DataFrame that simulation.generate_profiles() produces (columns:
hospital_kw, academic_kw, ev_kw, facility_kw, solar_kw; indexed by
minute). In the finished system this would instead be a rolling buffer of
recent real telemetry -- the forecasting logic itself doesn't care where
the rows came from, only that they're actual past readings.
"""

import numpy as np
import pandas as pd
import pandapower as pp

from grid_engine.grid import build_campus_network, get_asset_indices, run_power_flow
from grid_engine.simulation import apply_profile_row, run_time_series

ASSET_KW_COLUMNS = ["hospital_kw", "academic_kw", "ev_kw", "facility_kw", "solar_kw"]

# Tracked feeders/transformer -- kept as a constant so forecasting.py and
# any future scenarios.py violation logic agree on what "the network's
# constraints" means.
LOADING_COLUMNS = ["F1_loading_pct", "F2_loading_pct", "F3_loading_pct", "trafo_loading_pct"]

# Milestone 4: which named constraint (matching the GridState schema's
# prediction.constraint values) each loading column corresponds to, and
# which bus relief should be modeled at if that constraint is violated.
# F1/F2/F3 relief is modeled at the feeder's own downstream bus; the
# transformer serves the whole campus, so its relief is modeled at the
# shared campus_main bus (upstream of all three feeders).
CONSTRAINT_NAMES = {
    "F1_loading_pct": "F1_OVERLOAD",
    "F2_loading_pct": "F2_OVERLOAD",
    "F3_loading_pct": "F3_OVERLOAD",
    "trafo_loading_pct": "TRANSFORMER_OVERLOAD",
}
CONSTRAINT_RELIEF_BUS = {
    "F1_loading_pct": "hospital",
    "F2_loading_pct": "academic",
    "F3_loading_pct": "facility",
    "trafo_loading_pct": "campus_main",
}


def forecast_linear_trend(
    history: pd.DataFrame,
    horizon_min: int = 20,
    trailing_window_min: int = 10,
    dt_min: int = 1,
) -> pd.DataFrame:
    """
    Fit a simple linear trend to each asset's recent kW history and
    extrapolate it forward.

    `history` must be indexed by minute (ascending) with the columns in
    ASSET_KW_COLUMNS -- e.g. a slice of simulation.generate_profiles()'s
    output, or a rolling buffer of real telemetry with the same shape.

    Only the trailing `trailing_window_min` minutes of `history` are used
    for the fit (recent trend matters more than the whole day), and the
    forecast covers `now+dt_min` through `now+horizon_min` where `now` is
    `history`'s last (most recent) minute.

    Forecasted kW values are clipped at 0 (can't generate/consume negative
    power in this simple model).
    """
    missing = set(ASSET_KW_COLUMNS) - set(history.columns)
    if missing:
        raise ValueError(f"history is missing required columns: {sorted(missing)}")
    if len(history) < 2:
        raise ValueError("need at least 2 rows of history to fit a trend")

    now_minute = history.index.max()
    window = history.loc[history.index > now_minute - trailing_window_min]
    if len(window) < 2:
        raise ValueError(
            f"only {len(window)} history row(s) fall within the trailing "
            f"{trailing_window_min}-minute window -- need at least 2 to fit a trend"
        )

    future_minutes = np.arange(now_minute + dt_min, now_minute + horizon_min + dt_min, dt_min)
    x = window.index.values.astype(float)

    forecast = {}
    for col in ASSET_KW_COLUMNS:
        slope, intercept = np.polyfit(x, window[col].values, deg=1)
        forecast[col] = np.clip(slope * future_minutes + intercept, 0, None)

    return pd.DataFrame(forecast, index=pd.Index(future_minutes, name="minute"))


def forecast_grid_states(
    history: pd.DataFrame,
    horizon_min: int = 20,
    trailing_window_min: int = 10,
    dt_min: int = 1,
) -> pd.DataFrame:
    """
    Forecast forward from `history` and run AC power flow on each
    forecasted minute, on a fresh Milestone-1 network. Returns the same
    shape as simulation.run_time_series(): a results DataFrame indexed by
    (future) minute with feeder/transformer loading and bus voltages.

    Uses a *fresh* build_campus_network() rather than reusing whatever
    network object the caller has, because a forecast is a hypothetical --
    it must never mutate a live/current network state. (Topology is
    assumed unchanged from Milestone 1 here; outages/islanding are
    Milestone 6 scope and are not something this forecaster anticipates.)
    """
    forecast_profiles = forecast_linear_trend(history, horizon_min, trailing_window_min, dt_min)
    net = build_campus_network()
    return run_time_series(net, forecast_profiles)


def compute_required_relief_kw(
    profile_row: pd.Series,
    relief_bus_name: str,
    target_col: str,
    limit_pct: float = 100.0,
    max_relief_kw: float = 300.0,
    tolerance_kw: float = 0.5,
) -> float | None:
    """
    Milestone 4: binary-search the minimum amount of "relief" -- extra
    local supply or demand reduction, modeled as an ideal unity-power-
    factor injection at `relief_bus_name` -- that brings `target_col`
    (one of LOADING_COLUMNS) back to `limit_pct` or below, given the
    network at the forecasted demand/generation levels in `profile_row`.

    This deliberately does NOT decide *which* asset provides the relief
    (shed academic load, delay EV charging, discharge the BESS, ...) --
    that assignment across specific flexible assets is scenarios.py /
    Person 2's job (Milestone 5). This only answers "how much, in total,
    in kW, would be enough" -- and answers it by actually rerunning power
    flow at each trial value, not by a linear estimate, since the
    relationship between injected kW and loading% is not exactly linear
    (voltage-dependent losses).

    Returns None if even `max_relief_kw` isn't enough within the search
    bound (the caller should widen it, or this may not be a constraint
    demand-side relief alone can fix -- e.g. it needs a topology change).
    """
    def loading_at(relief_kw: float) -> float:
        net = build_campus_network()
        indices = get_asset_indices(net)
        apply_profile_row(net, profile_row, indices)
        bus_idx = net.bus.index[net.bus["name"] == relief_bus_name][0]
        pp.create_sgen(net, bus=bus_idx, p_mw=relief_kw / 1000.0, q_mvar=0.0, name="relief")
        run_power_flow(net)
        if target_col == "trafo_loading_pct":
            return net.res_trafo["loading_percent"].iloc[0]
        line_name = target_col.replace("_loading_pct", "")
        return net.res_line.loc[net.line["name"] == line_name, "loading_percent"].iloc[0]

    if loading_at(0.0) <= limit_pct:
        return 0.0
    if loading_at(max_relief_kw) > limit_pct:
        return None

    lo, hi = 0.0, max_relief_kw
    while hi - lo > tolerance_kw:
        mid = (lo + hi) / 2.0
        if loading_at(mid) <= limit_pct:
            hi = mid
        else:
            lo = mid
    return round(hi, 1)


def detect_predicted_violation(
    history: pd.DataFrame,
    horizon_min: int = 20,
    trailing_window_min: int = 10,
    limit_pct: float = 100.0,
) -> dict | None:
    """
    Milestone 4: scan the forecast horizon for the first predicted feeder/
    transformer loading constraint crossing, and compute how much relief
    would be needed to keep it under `limit_pct`.

    Returns a dict matching the shape of the GridState schema's
    "prediction" object (shared/schemas/grid_state.json), e.g.:

        {"constraint": "F2_OVERLOAD", "predicted_loading_pct": 102.6,
         "time_to_violation_min": 15, "required_relief_kw": 24.5}

    or None if nothing is predicted to cross `limit_pct` within the
    horizon.

    DESIGN NOTE on "predicted_loading_pct" / "required_relief_kw": these
    two are computed at the WORST (peak) point the violated constraint
    reaches anywhere in the forecast horizon, not at the exact instant it
    first crosses `limit_pct`. "time_to_violation_min" is still the first-
    crossing lead time (the early-warning number). Sizing relief only for
    the marginal first-crossing moment would understate what's needed --
    loading keeps climbing after that instant in this scenario, so relief
    that just barely clears the first crossing would put the feeder right
    back in violation minutes later. Sizing for the horizon's worst point
    gives a stable target Person 2's market can actually act on. Flagging
    this as a judgment call, not an obvious "correct" answer -- if the
    team wants relief sized for the first-crossing instant instead, that's
    a one-line change (use `violation_minute` instead of `peak_minute`
    below).

    SCOPE NOTE: only feeder/transformer LOADING constraints are covered
    here, matching the schema's own worked example exactly. Bus voltage
    (p.u.) limits are computed and available in forecast_grid_states()'s
    output, but folding a p.u.-voltage violation into this same single
    "constraint" + "required_relief_kw" shape would mean ranking two
    different physical units (% loading vs p.u. voltage) against each
    other without the schema specifying how to compare them. Rather than
    guess at that, this is flagged as a reasonable future extension --
    smallest compatible change would likely be a second, parallel
    "voltage_prediction" object -- for the team to confirm before it's
    added.
    """
    now_minute = history.index.max()
    forecast_profiles = forecast_linear_trend(history, horizon_min, trailing_window_min)
    forecast_results = forecast_grid_states(history, horizon_min, trailing_window_min)

    over_limit = forecast_results[(forecast_results[LOADING_COLUMNS] > limit_pct).any(axis=1)]
    if over_limit.empty:
        return None

    violation_minute = over_limit.index[0]
    row = over_limit.iloc[0]
    violated_cols = [c for c in LOADING_COLUMNS if row[c] > limit_pct]
    worst_col = max(violated_cols, key=lambda c: row[c])  # report the most-violated constraint

    # Size predicted_loading_pct / required_relief_kw off the WORST point
    # this constraint reaches anywhere in the forecast horizon, not just
    # the instant it first crosses the limit -- see the design note above.
    peak_minute = forecast_results[worst_col].idxmax()
    peak_value = forecast_results.loc[peak_minute, worst_col]

    constraint_name = CONSTRAINT_NAMES[worst_col]
    relief_bus = CONSTRAINT_RELIEF_BUS[worst_col]
    profile_row = forecast_profiles.loc[peak_minute]
    required_relief_kw = compute_required_relief_kw(profile_row, relief_bus, worst_col, limit_pct)

    return {
        "constraint": constraint_name,
        "predicted_loading_pct": round(float(peak_value), 1),
        "time_to_violation_min": int(violation_minute - now_minute),
        "required_relief_kw": required_relief_kw,
    }


def current_grid_state(history: pd.DataFrame) -> pd.Series:
    """
    Run power flow on just the most recent row of `history` (on a fresh
    network) to get "right now"'s computed loading/voltage state, in the
    same units/columns as forecast_grid_states()'s output. Useful as the
    "NOW: F2=X%" baseline to compare a forecast against.
    """
    now_row = history.iloc[[-1]]
    net = build_campus_network()
    result = run_time_series(net, now_row)
    return result.iloc[0]


def summarize_forecast(history: pd.DataFrame, horizon_min: int = 20, trailing_window_min: int = 10) -> None:
    """
    Print a "NOW -> forecast" trajectory for the tracked feeders/
    transformer, and the first predicted constraint crossing (if any)
    within the forecast horizon, with its lead time.
    """
    now_minute = history.index.max()
    now_state = current_grid_state(history)
    forecast = forecast_grid_states(history, horizon_min, trailing_window_min)

    print("=" * 60)
    print(f"FORECAST FROM t={now_minute} min (trailing {trailing_window_min} min trend, "
          f"{horizon_min} min horizon)")
    print("=" * 60)
    print(f"NOW (t={now_minute}):")
    for col in LOADING_COLUMNS:
        print(f"  {col:20s} {now_state[col]:6.1f}%")

    print()
    print("FORECAST TRAJECTORY:")
    checkpoints = [m for m in (now_minute + 5, now_minute + 10, now_minute + 15, now_minute + 20)
                   if m in forecast.index]
    for m in checkpoints:
        row = forecast.loc[m]
        lead = m - now_minute
        parts = ", ".join(f"{col.replace('_loading_pct','').replace('_',' ')}={row[col]:.1f}%"
                           for col in LOADING_COLUMNS)
        print(f"  +{lead:>2d} min (t={m}): {parts}")

    print()
    prediction = detect_predicted_violation(history, horizon_min, trailing_window_min)
    if prediction is not None:
        relief = prediction["required_relief_kw"]
        relief_str = f"{relief:.1f} kW" if relief is not None else "more than the search bound covers"
        print(f"PREDICTION: {prediction['constraint']} predicted to reach "
              f"{prediction['predicted_loading_pct']:.1f}% loading in "
              f"~{prediction['time_to_violation_min']} minutes.")
        print(f"REQUIRED RELIEF: ~{relief_str} of demand reduction / local supply at the "
              f"affected bus would keep it at or under 100%.")
    else:
        print(f"PREDICTION: no constraint predicted to exceed 100% within the "
              f"{horizon_min}-minute forecast horizon.")


if __name__ == "__main__":
    from grid_engine.simulation import generate_profiles

    # Demo: pretend "now" is t=100 minutes into the synthetic 3-hour run,
    # with only the last 10 minutes of history available (matching
    # trailing_window_min's default). t=100 is a representative point on
    # this run's cloud-dip trend -- chosen because it shows a meaningful
    # ~15 minute predictive lead time before feeder F2 actually crosses
    # 100% (around t=115 in the underlying simulated series), which is a
    # good demonstration of what this milestone is for. It is NOT a
    # hardcoded result: every number below is computed by rerunning power
    # flow on the forecasted state, same as any other "now" would be.
    NOW_MINUTE = 100
    full_profiles = generate_profiles()
    history = full_profiles.loc[full_profiles.index <= NOW_MINUTE]

    summarize_forecast(history, horizon_min=20, trailing_window_min=10)
