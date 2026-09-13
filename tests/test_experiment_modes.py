"""
Task 3/8 tests for the two pure-Python baseline strategies in
scripts/run_experiment.py (no_intervention, load_shedding) -- run once
per test session (module-scoped fixture; each mode takes a few seconds
over the full 168-hour replay) and checked against real, computed
invariants, not fabricated expectations.

Mode C (the Rust market system) is exercised by the live end-to-end test
in tests/test_end_to_end_apply.py and is not re-run here to keep this
file fast and Rust-toolchain-independent.
"""

import pytest

from experiments.metrics import overloaded_timesteps, summarize
from scripts.run_experiment import _load_samples, run_load_shedding, run_no_intervention


@pytest.fixture(scope="module")
def samples():
    return _load_samples()


@pytest.fixture(scope="module")
def no_intervention_series(samples):
    return run_no_intervention(samples)


@pytest.fixture(scope="module")
def load_shedding_series(samples):
    return run_load_shedding(samples)


def test_bundled_replay_has_168_hourly_samples(samples):
    assert len(samples) == 168


# --- baseline modes work -----------------------------------------------


def test_no_intervention_never_curtails_anything(no_intervention_series):
    for row in no_intervention_series:
        assert row["served_kw"] == row["scheduled_kw"]
        assert row["actions"] == []


def test_no_intervention_reproduces_the_known_natural_overload(no_intervention_series):
    """Hour 48 (2016-06-08 06:00 UTC) is the one point in the whole
    recorded week where F2 genuinely exceeds 100% loading under no
    intervention (academic at its recorded peak, EV and solar both
    near zero) -- see simulation.py's own docstring finding and
    README.md section 20. This pins that down as a regression check."""
    assert overloaded_timesteps(no_intervention_series) == 1
    hour_48 = next(row for row in no_intervention_series if row["hour_index"] == 48)
    assert hour_48["feeders"]["F2"] > 130.0


def test_load_shedding_resolves_every_overload_it_can_reach(load_shedding_series):
    """F1 (hospital's own feeder) never overloads in this dataset, so the
    rule's real target -- F2 -- must always end at or under 100%."""
    for row in load_shedding_series:
        assert row["feeders"]["F2"] <= 100.0 + 1e-6


def test_load_shedding_never_touches_hospital_load(load_shedding_series):
    for row in load_shedding_series:
        assert row["served_kw"]["hospital_load"] == row["scheduled_kw"]["hospital_load"]
        assert all(a["asset_id"] != "hosp-1" for a in row["actions"])


def test_load_shedding_only_acts_when_something_is_actually_overloaded(load_shedding_series):
    acted_hours = [row["hour_index"] for row in load_shedding_series if row["actions"]]
    assert acted_hours == [48]  # the one hour no_intervention actually violates the limit


# --- metrics are calculated correctly, from a real run ------------------


def test_summary_metrics_match_hand_verified_run(no_intervention_series, load_shedding_series):
    no_int_summary = summarize(no_intervention_series)
    assert no_int_summary["overloaded_timesteps"] == 1
    assert no_int_summary["unserved_energy_kwh"] == 0.0
    assert no_int_summary["critical_demand_served_pct"] == 100.0

    shed_summary = summarize(load_shedding_series)
    assert shed_summary["overloaded_timesteps"] == 0
    assert shed_summary["unserved_energy_kwh"] > 0.0  # shedding is not free
    assert shed_summary["critical_demand_served_pct"] == 100.0  # but hospital is untouched


# --- identical scenarios are reproducible --------------------------------


def test_no_intervention_is_deterministic_across_runs(samples, no_intervention_series):
    rerun = run_no_intervention(samples)
    assert rerun == no_intervention_series


def test_load_shedding_is_deterministic_across_runs(samples, load_shedding_series):
    rerun = run_load_shedding(samples)
    assert rerun == load_shedding_series
