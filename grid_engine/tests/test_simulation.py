"""
Milestone 2 sanity tests for grid_engine.simulation.

Profiles are deterministic (seeded), so we can assert on the specific
shape of the story: normal at the start, no permanent runaway violation,
and power flow converges throughout. We do NOT assert exact loading
percentages -- those are sensitive to the assumed profile parameters and
are expected to be retuned as the project evolves.
"""

import numpy as np

from grid_engine.grid import build_campus_network
from grid_engine.simulation import generate_profiles, run_time_series


def test_generate_profiles_shape_and_columns():
    profiles = generate_profiles(horizon_min=180, dt_min=1)
    assert len(profiles) == 181  # inclusive of both endpoints, 1-minute steps
    expected_cols = {"hospital_kw", "academic_kw", "ev_kw", "facility_kw", "solar_kw"}
    assert expected_cols.issubset(set(profiles.columns))
    assert profiles.index.name == "minute"


def test_generate_profiles_is_deterministic():
    profiles_a = generate_profiles(seed=42)
    profiles_b = generate_profiles(seed=42)
    assert np.allclose(profiles_a.values, profiles_b.values)


def test_generate_profiles_no_negative_power():
    profiles = generate_profiles()
    assert (profiles >= 0).all().all()


def test_run_time_series_all_steps_converge():
    net = build_campus_network()
    profiles = generate_profiles(horizon_min=60)  # shorter run for test speed
    results = run_time_series(net, profiles)
    assert len(results) == 61
    assert results["converged"].all()


def test_run_time_series_starts_normal():
    """
    With the default seed, the first several minutes (well before the
    t~105-130 cloud dip) should show F2 under 100% loading -- the baseline
    should read as normal, per the project's prediction philosophy (present
    state OK, violations are what gets *forecast*).
    """
    net = build_campus_network()
    profiles = generate_profiles()
    results = run_time_series(net, profiles)
    assert (results.loc[0:20, "F2_loading_pct"] < 100).all()


def test_run_time_series_violation_is_bounded_not_permanent():
    """The synthetic cloud dip should push F2 over 100% for a bounded
    stretch, not turn the entire back half of the run into one permanent
    violation -- otherwise this isn't a useful 'predicted future violation'
    demo scenario."""
    net = build_campus_network()
    profiles = generate_profiles()
    results = run_time_series(net, profiles)
    over_limit_count = (results["F2_loading_pct"] > 100).sum()
    assert 0 < over_limit_count < len(results) * 0.5
    # and the run should recover by the end of the window
    assert results["F2_loading_pct"].iloc[-1] < 100
