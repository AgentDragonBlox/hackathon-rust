"""Task 4/8 tests: metrics are calculated correctly, against small
hand-computed synthetic series -- no live services or pandapower needed
(experiments/metrics.py is deliberately pure)."""

from experiments.metrics import (
    action_counts,
    critical_demand_served_pct,
    curtailed_energy_kwh,
    decision_latency_stats,
    final_battery_soc_percent,
    overloaded_timesteps,
    peak_overload_pct,
    summarize,
    unserved_energy_kwh,
)


def _row(hour, feeders, scheduled, served, battery_soc=72.0, actions=None,
         proposed=0, accepted=0, rejected=0, settled=0, latency=None):
    return {
        "hour_index": hour, "timestamp": f"t{hour}", "feeders": feeders,
        "scheduled_kw": scheduled, "served_kw": served, "battery_soc_percent": battery_soc,
        "actions": actions or [], "proposed": proposed, "accepted": accepted,
        "rejected": rejected, "settled": settled, "decision_latency_ms": latency,
    }


def test_overloaded_timesteps_counts_hours_with_any_feeder_over_limit():
    series = [
        _row(0, {"F1": 50, "F2": 90}, {}, {}),
        _row(1, {"F1": 50, "F2": 136.8}, {}, {}),  # overloaded
        _row(2, {"F1": 101, "F2": 50}, {}, {}),    # overloaded on a different feeder
    ]
    assert overloaded_timesteps(series) == 2


def test_peak_overload_pct_is_the_largest_excess_above_limit():
    series = [
        _row(0, {"F1": 50}, {}, {}),
        _row(1, {"F1": 136.8}, {}, {}),
        _row(2, {"F1": 105.0}, {}, {}),
    ]
    assert peak_overload_pct(series) == 36.8


def test_peak_overload_pct_is_zero_when_never_exceeded():
    series = [_row(0, {"F1": 50, "F2": 99.9}, {}, {})]
    assert peak_overload_pct(series) == 0.0


def test_critical_demand_served_pct_full_when_never_curtailed():
    series = [
        _row(0, {}, {"hospital_load": 380}, {"hospital_load": 380}),
        _row(1, {}, {"hospital_load": 380}, {"hospital_load": 380}),
    ]
    assert critical_demand_served_pct(series) == 100.0


def test_critical_demand_served_pct_reflects_real_curtailment():
    series = [
        _row(0, {}, {"hospital_load": 380}, {"hospital_load": 380}),
        _row(1, {}, {"hospital_load": 380}, {"hospital_load": 361}),  # 5% shed, matches hospital policy
    ]
    # (380 + 361) / (380 + 380) * 100
    assert critical_demand_served_pct(series) == round(741 / 760 * 100, 3)


def test_unserved_energy_sums_shortfall_across_all_assets_and_hours():
    series = [
        _row(0, {}, {"academic_load": 240, "ev_load": 70}, {"academic_load": 212, "ev_load": 70}),
        _row(1, {}, {"academic_load": 240, "ev_load": 70}, {"academic_load": 240, "ev_load": 56}),
    ]
    # hour0: 28kWh short on academic; hour1: 14kWh short on ev -> 42kWh total (1h samples)
    assert unserved_energy_kwh(series) == 42.0


def test_unserved_energy_is_zero_for_pure_no_intervention_series():
    series = [_row(0, {}, {"academic_load": 240}, {"academic_load": 240})]
    assert unserved_energy_kwh(series) == 0.0


def test_curtailed_energy_counts_only_shedding_actions_not_battery_discharge():
    series = [
        _row(0, {}, {}, {}, actions=[
            {"asset_id": "acad-1", "action_type": "hvac_reduction", "kw_amount": 28.0},
            {"asset_id": "batt-1", "action_type": "battery_discharge", "kw_amount": 30.0},
        ]),
    ]
    assert curtailed_energy_kwh(series) == 28.0


def test_final_battery_soc_percent_is_the_last_hours_value():
    series = [_row(0, {}, {}, {}, battery_soc=72.0), _row(1, {}, {}, {}, battery_soc=68.5)]
    assert final_battery_soc_percent(series) == 68.5


def test_final_battery_soc_percent_none_for_empty_series():
    assert final_battery_soc_percent([]) is None


def test_action_counts_sums_each_field_across_hours():
    series = [
        _row(0, {}, {}, {}, proposed=2, accepted=1, rejected=1, settled=1),
        _row(1, {}, {}, {}, proposed=1, accepted=1, rejected=0, settled=1),
    ]
    assert action_counts(series) == {"proposed": 3, "accepted": 2, "rejected": 1, "settled": 2}


def test_decision_latency_stats_ignores_hours_with_no_market_and_reports_none_if_no_market_ever_ran():
    baseline_series = [_row(0, {}, {}, {}, latency=None), _row(1, {}, {}, {}, latency=None)]
    stats = decision_latency_stats(baseline_series)
    assert stats == {"avg_ms": None, "max_ms": None, "samples": 0}

    market_series = [_row(0, {}, {}, {}, latency=None), _row(1, {}, {}, {}, latency=40.0), _row(2, {}, {}, {}, latency=60.0)]
    stats2 = decision_latency_stats(market_series)
    assert stats2 == {"avg_ms": 50.0, "max_ms": 60.0, "samples": 2}


def test_summarize_bundles_every_metric():
    series = [_row(0, {"F2": 136.8}, {"hospital_load": 380}, {"hospital_load": 380},
                    battery_soc=72.0, proposed=1, accepted=1, settled=1, latency=55.0)]
    result = summarize(series)
    assert result["overloaded_timesteps"] == 1
    assert result["peak_overload_pct"] == 36.8
    assert result["critical_demand_served_pct"] == 100.0
    assert result["final_battery_soc_percent"] == 72.0
    assert result["actions"] == {"proposed": 1, "accepted": 1, "rejected": 0, "settled": 1}
    assert result["decision_latency"]["avg_ms"] == 55.0
    assert result["hours_simulated"] == 1
