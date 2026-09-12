"""
Milestone 3 sanity tests for grid_engine.forecasting.
"""

import numpy as np
import pandas as pd
import pytest

from grid_engine.forecasting import (
    ASSET_KW_COLUMNS,
    compute_required_relief_kw,
    current_grid_state,
    detect_predicted_violation,
    forecast_grid_states,
    forecast_linear_trend,
)
from grid_engine.grid import build_campus_network, get_asset_indices, run_power_flow
from grid_engine.simulation import apply_profile_row, generate_profiles


def test_forecast_linear_trend_shape():
    profiles = generate_profiles(horizon_min=60)
    history = profiles.loc[:40]
    forecast = forecast_linear_trend(history, horizon_min=20, trailing_window_min=10)
    assert len(forecast) == 20
    assert forecast.index.min() == 41
    assert forecast.index.max() == 60
    assert set(ASSET_KW_COLUMNS).issubset(set(forecast.columns))


def test_forecast_linear_trend_extrapolates_a_clear_trend():
    """A perfectly linear synthetic history should extrapolate almost
    exactly (well within noise-free floating point tolerance)."""
    minutes = np.arange(0, 21)
    history = pd.DataFrame(
        {col: 100.0 + 2.0 * minutes for col in ASSET_KW_COLUMNS},
        index=pd.Index(minutes, name="minute"),
    )
    forecast = forecast_linear_trend(history, horizon_min=5, trailing_window_min=10)
    expected_at_25 = 100.0 + 2.0 * 25
    assert forecast.loc[25, "academic_kw"] == pytest.approx(expected_at_25, rel=1e-6)


def test_forecast_linear_trend_clips_negative_to_zero():
    """A steeply falling trend shouldn't extrapolate to negative kW."""
    minutes = np.arange(0, 11)
    history = pd.DataFrame(
        {col: 50.0 - 10.0 * minutes for col in ASSET_KW_COLUMNS},
        index=pd.Index(minutes, name="minute"),
    )
    forecast = forecast_linear_trend(history, horizon_min=10, trailing_window_min=10)
    assert (forecast >= 0).all().all()


def test_forecast_linear_trend_raises_on_missing_columns():
    bad_history = pd.DataFrame({"hospital_kw": [1.0, 2.0]}, index=pd.Index([0, 1], name="minute"))
    with pytest.raises(ValueError):
        forecast_linear_trend(bad_history)


def test_forecast_linear_trend_raises_on_insufficient_history():
    profiles = generate_profiles(horizon_min=5)
    history = profiles.loc[:0]  # single row
    with pytest.raises(ValueError):
        forecast_linear_trend(history)


def test_forecast_grid_states_returns_same_shape_as_current_grid_state():
    profiles = generate_profiles(horizon_min=60)
    history = profiles.loc[:40]
    now = current_grid_state(history)
    forecast = forecast_grid_states(history, horizon_min=10, trailing_window_min=10)
    assert set(forecast.columns) == set(now.index)


def test_default_demo_scenario_predicts_f2_violation_with_lead_time():
    """
    Regression test for the specific demo scenario used in
    forecasting.py's __main__: at t=100 (default seed), the forecaster
    should predict F2 crossing 100% a few minutes ahead of "now" but
    within the 20-minute horizon -- i.e. it should show genuine lead
    time, not an immediate or a missed prediction.
    """
    profiles = generate_profiles()
    history = profiles.loc[profiles.index <= 100]
    forecast = forecast_grid_states(history, horizon_min=20, trailing_window_min=10)
    over_limit = forecast[forecast["F2_loading_pct"] > 100]
    assert not over_limit.empty, "expected F2 to be predicted to cross 100% within the horizon"
    lead_time = over_limit.index[0] - 100
    assert 1 <= lead_time <= 20


# --- Milestone 4: violation detection + required relief --------------------

def test_detect_predicted_violation_returns_none_when_nothing_crosses():
    """A short, flat-ish window well before the cloud dip shouldn't predict
    any violation."""
    profiles = generate_profiles()
    history = profiles.loc[profiles.index <= 30]
    result = detect_predicted_violation(history, horizon_min=10, trailing_window_min=10)
    assert result is None


def test_detect_predicted_violation_matches_schema_shape():
    profiles = generate_profiles()
    history = profiles.loc[profiles.index <= 100]
    result = detect_predicted_violation(history, horizon_min=20, trailing_window_min=10)
    assert result is not None
    assert set(result.keys()) == {
        "constraint", "predicted_loading_pct", "time_to_violation_min", "required_relief_kw",
    }
    assert result["constraint"] == "F2_OVERLOAD"
    assert result["predicted_loading_pct"] > 100
    assert 1 <= result["time_to_violation_min"] <= 20
    assert result["required_relief_kw"] is not None
    assert result["required_relief_kw"] > 0


def test_compute_required_relief_kw_returns_zero_when_already_within_limit():
    profiles = generate_profiles()
    row = profiles.loc[0]  # baseline minute, F2 well under 100%
    relief = compute_required_relief_kw(row, "academic", "F2_loading_pct", limit_pct=100.0)
    assert relief == 0.0


def test_compute_required_relief_kw_actually_resolves_the_constraint():
    """The computed relief, when actually applied to the network, should
    bring loading back under the limit -- verified by rerunning power flow
    with that relief applied, not just trusting the search."""
    profiles = generate_profiles()
    history = profiles.loc[profiles.index <= 100]
    prediction = detect_predicted_violation(history, horizon_min=20, trailing_window_min=10)
    assert prediction is not None
    relief_kw = prediction["required_relief_kw"]
    assert relief_kw is not None

    # Rebuild the peak-violation profile row independently and confirm
    # applying the computed relief clears the constraint.
    from grid_engine.forecasting import forecast_linear_trend
    forecast_profiles = forecast_linear_trend(history, horizon_min=20, trailing_window_min=10)
    forecast_results = forecast_grid_states(history, horizon_min=20, trailing_window_min=10)
    peak_minute = forecast_results["F2_loading_pct"].idxmax()
    profile_row = forecast_profiles.loc[peak_minute]

    net = build_campus_network()
    indices = get_asset_indices(net)
    apply_profile_row(net, profile_row, indices)
    academic_bus = net.bus.index[net.bus["name"] == "academic"][0]
    import pandapower as pp
    pp.create_sgen(net, bus=academic_bus, p_mw=relief_kw / 1000.0, q_mvar=0.0, name="relief")
    run_power_flow(net)
    f2_loading = net.res_line.loc[net.line["name"] == "F2", "loading_percent"].iloc[0]
    assert f2_loading <= 100.0 + 1e-6
