"""
Milestone 6 tests for grid_engine.faults -- simulated fault injection,
verified against real (re)solved power flow and real topology analysis,
never assumed.

Baseline (build_campus_network() + run_power_flow(), default state):
  F1=71.0%, F2=93.3%, F3=85.0%, transformer=77.8%
  hospital_load=380kW, academic_load=240kW, ev_load=70kW, facility_load=150kW
  solar-1=165kW, hospital_bess: max_p_mw=0.25, soc_percent=72.0
"""

import copy

import pandapower as pp
import pytest

from grid_engine.grid import build_campus_network, get_asset_indices, run_power_flow
from grid_engine.faults import (
    ASSUMED_DEMAND_SPIKE_FRACTION,
    ASSUMED_SOLAR_DROP_REMAINING_FRACTION,
    FEEDER_OVERLOAD_TARGET_PCT,
    FaultApplicationError,
    apply_fault,
    compute_islands,
)


@pytest.fixture
def solved_net():
    net = build_campus_network()
    run_power_flow(net)
    return net


# --- line_fault --------------------------------------------------------


def test_line_fault_disables_the_named_line(solved_net):
    net = copy.deepcopy(solved_net)
    apply_fault(net, "F1", "line_fault")
    idx = net.line.index[net.line["name"] == "F1"][0]
    assert net.line.loc[idx, "in_service"] == False  # noqa: E712


def test_line_fault_islands_the_downstream_bus_and_assets(solved_net):
    net = copy.deepcopy(solved_net)
    apply_fault(net, "F1", "line_fault")
    run_power_flow(net)
    islands = compute_islands(net)
    assert islands == [["batt-1", "hosp-1"]]


def test_line_fault_islanded_assets_report_zero_power_not_nan(solved_net):
    """Real pandapower behavior (confirmed by direct experiment): an
    in-service load on an unreachable bus reports 0 p_mw, not NaN."""
    net = copy.deepcopy(solved_net)
    apply_fault(net, "F1", "line_fault")
    run_power_flow(net)
    idx = net.load.index[net.load["name"] == "hospital_load"][0]
    assert net.res_load.loc[idx, "p_mw"] == pytest.approx(0.0)


def test_line_fault_invalid_feeder_id_raises(solved_net):
    with pytest.raises(FaultApplicationError, match="feeder_id"):
        apply_fault(copy.deepcopy(solved_net), "F9", "line_fault")


# --- grid_outage ---------------------------------------------------------


def test_grid_outage_disables_ext_grid(solved_net):
    net = copy.deepcopy(solved_net)
    apply_fault(net, "F1", "grid_outage")  # feeder_id ignored -- see module docstring
    assert net.ext_grid.loc[0, "in_service"] == False  # noqa: E712


def test_grid_outage_has_no_power_flow_solution(solved_net):
    """Confirmed by direct experiment: with no reference bus, pandapower
    raises UserWarning (not LoadflowNotConverged) and clears every res_*
    table to NaN."""
    net = copy.deepcopy(solved_net)
    apply_fault(net, "F2", "grid_outage")
    with pytest.raises(UserWarning):
        run_power_flow(net)
    assert net.res_bus["vm_pu"].isna().all()


def test_grid_outage_islands_the_entire_campus(solved_net):
    """No reference bus anywhere -> the whole campus is one island,
    regardless of which feeder_id was submitted (it's not used)."""
    net = copy.deepcopy(solved_net)
    apply_fault(net, "F3", "grid_outage")
    islands = compute_islands(net)
    assert islands == [["acad-1", "batt-1", "ev-1", "fac-1", "hosp-1", "solar-1"]]


def test_grid_outage_accepts_any_valid_feeder_id_with_identical_effect(solved_net):
    net_a = copy.deepcopy(solved_net)
    net_b = copy.deepcopy(solved_net)
    apply_fault(net_a, "F1", "grid_outage")
    apply_fault(net_b, "F3", "grid_outage")
    assert bool(net_a.ext_grid.loc[0, "in_service"]) == bool(net_b.ext_grid.loc[0, "in_service"]) == False  # noqa: E712


def test_grid_outage_invalid_feeder_id_raises(solved_net):
    with pytest.raises(FaultApplicationError):
        apply_fault(copy.deepcopy(solved_net), "NOT_A_FEEDER", "grid_outage")


# --- battery_failure -------------------------------------------------------


def test_battery_failure_takes_storage_offline(solved_net):
    net = copy.deepcopy(solved_net)
    apply_fault(net, "F1", "battery_failure")
    indices = get_asset_indices(net)
    _, idx = indices["hospital_bess"]
    assert net.storage.loc[idx, "in_service"] == False  # noqa: E712


def test_battery_failure_requires_feeder_f1(solved_net):
    with pytest.raises(FaultApplicationError, match="F1"):
        apply_fault(copy.deepcopy(solved_net), "F2", "battery_failure")


def test_battery_failure_offline_battery_delivers_zero_real_power(solved_net):
    net = copy.deepcopy(solved_net)
    apply_fault(net, "F1", "battery_failure")
    run_power_flow(net)
    indices = get_asset_indices(net)
    _, idx = indices["hospital_bess"]
    assert net.res_storage.loc[idx, "p_mw"] == pytest.approx(0.0)


# --- solar_drop --------------------------------------------------------


def test_solar_drop_reduces_sgen_by_assumed_fraction(solved_net):
    net = copy.deepcopy(solved_net)
    indices = get_asset_indices(net)
    _, idx = indices["solar"]
    before = float(net.sgen.loc[idx, "p_mw"])
    apply_fault(net, "F2", "solar_drop")
    after = float(net.sgen.loc[idx, "p_mw"])
    assert after == pytest.approx(before * ASSUMED_SOLAR_DROP_REMAINING_FRACTION)


def test_solar_drop_requires_feeder_f2(solved_net):
    with pytest.raises(FaultApplicationError, match="F2"):
        apply_fault(copy.deepcopy(solved_net), "F1", "solar_drop")


def test_solar_drop_pushes_f2_toward_overload(solved_net):
    """Losing most of F2's local generation means more of its load has
    to be served from upstream -- real, verified effect, not assumed."""
    net = copy.deepcopy(solved_net)
    apply_fault(net, "F2", "solar_drop")
    run_power_flow(net)
    f2_idx = net.line.index[net.line["name"] == "F2"][0]
    baseline_f2 = float(solved_net.res_line.loc[f2_idx, "loading_percent"])
    after_f2 = float(net.res_line.loc[f2_idx, "loading_percent"])
    assert after_f2 > baseline_f2


# --- demand_spike --------------------------------------------------------


def test_demand_spike_scales_feeder_loads_by_assumed_fraction(solved_net):
    net = copy.deepcopy(solved_net)
    idx = net.load.index[net.load["name"] == "facility_load"][0]
    before = float(net.load.loc[idx, "p_mw"])
    apply_fault(net, "F3", "demand_spike")
    after = float(net.load.loc[idx, "p_mw"])
    assert after == pytest.approx(before * (1.0 + ASSUMED_DEMAND_SPIKE_FRACTION))


def test_demand_spike_does_not_touch_other_feeders(solved_net):
    net = copy.deepcopy(solved_net)
    idx = net.load.index[net.load["name"] == "academic_load"][0]
    before = float(net.load.loc[idx, "p_mw"])
    apply_fault(net, "F3", "demand_spike")
    after = float(net.load.loc[idx, "p_mw"])
    assert after == pytest.approx(before)


def test_demand_spike_invalid_feeder_id_raises(solved_net):
    with pytest.raises(FaultApplicationError):
        apply_fault(copy.deepcopy(solved_net), "F9", "demand_spike")


# --- feeder_overload -------------------------------------------------------


@pytest.mark.parametrize("feeder_id", ["F1", "F2", "F3"])
def test_feeder_overload_actually_exceeds_the_target_when_verified(solved_net, feeder_id):
    """The whole point of this fault type: don't trust the search, rerun
    power flow independently on the result and check the real number."""
    net = copy.deepcopy(solved_net)
    apply_fault(net, feeder_id, "feeder_overload")
    run_power_flow(net)
    line_idx = net.line.index[net.line["name"] == feeder_id][0]
    loading = float(net.res_line.loc[line_idx, "loading_percent"])
    assert loading >= FEEDER_OVERLOAD_TARGET_PCT
    assert loading < FEEDER_OVERLOAD_TARGET_PCT + 1.0  # found the smallest scale, not an overshoot


def test_feeder_overload_invalid_feeder_id_raises(solved_net):
    with pytest.raises(FaultApplicationError):
        apply_fault(copy.deepcopy(solved_net), "F9", "feeder_overload")


# --- apply_fault dispatch --------------------------------------------------


def test_apply_fault_unrecognized_type_raises(solved_net):
    with pytest.raises(FaultApplicationError, match="unrecognized fault_type"):
        apply_fault(copy.deepcopy(solved_net), "F1", "meteor_strike")


def test_apply_fault_does_not_mutate_a_net_it_is_not_given(solved_net):
    """Sanity check on the deep-copy discipline every caller is expected
    to follow (api.py's _rebuild_live_state does this)."""
    before = solved_net.load[["p_mw"]].copy()
    net = copy.deepcopy(solved_net)
    apply_fault(net, "F3", "demand_spike")
    assert solved_net.load[["p_mw"]].equals(before)


# --- compute_islands -------------------------------------------------------


def test_compute_islands_empty_on_healthy_baseline(solved_net):
    assert compute_islands(solved_net) == []


def test_compute_islands_groups_are_sorted(solved_net):
    net = copy.deepcopy(solved_net)
    apply_fault(net, "F1", "line_fault")
    run_power_flow(net)
    islands = compute_islands(net)
    assert islands[0] == sorted(islands[0])
