"""
Milestone 5 tests for grid_engine.scenarios -- validating Person 2's
ProposedAction objects against a real, rerun AC power flow.

Every "feasible"/"infeasible" assertion here is backed by an actual
pandapower solve, not an assumption -- these numbers were derived by
running the network and reading real results (see the baseline printed
in the module docstring notes below), the same way the rest of this
project's tests work.

Baseline (build_campus_network() + run_power_flow(), default state):
  F1=71.0%, F2=93.3%, F3=85.0%, transformer=77.8%
  bus voltages: utility_hv=1.0200, campus_main=1.0206, hospital=0.9672,
                academic=0.9517, facility=0.9566
  hospital_load=380kW, academic_load=240kW, ev_load=20kW
  hospital_bess: max_p_mw=0.25 (250kW), max_e_mwh=0.5, min_e_mwh=0.05,
                 soc_percent=72.0
"""

import copy

import pandapower as pp
import pytest

from grid_engine.grid import build_campus_network, run_power_flow
from grid_engine.scenarios import (
    _asset_capacity_kw,
    apply_proposed_action,
    check_constraints,
    validate_action,
    validate_actions,
)
from shared.contracts import ProposedAction, ValidationResult


@pytest.fixture
def solved_net():
    net = build_campus_network()
    run_power_flow(net)
    return net


def _action(asset_id, action_type, kw_amount, action_id="a1", request_id="r1"):
    return ProposedAction(
        action_id=action_id, request_id=request_id, asset_id=asset_id,
        action_type=action_type, kw_amount=kw_amount, source_offer_id="o1",
    )


# --- apply_proposed_action ---------------------------------------------


def test_apply_load_reduction_reduces_p_and_q_proportionally(solved_net):
    net = copy.deepcopy(solved_net)
    idx = net.load.index[net.load["name"] == "academic_load"][0]
    before_p = float(net.load.loc[idx, "p_mw"])
    before_q = float(net.load.loc[idx, "q_mvar"])
    apply_proposed_action(net, _action("acad-1", "hvac_reduction", 28.0))
    after_p = float(net.load.loc[idx, "p_mw"])
    after_q = float(net.load.loc[idx, "q_mvar"])
    assert after_p == pytest.approx(before_p - 0.028)
    # power factor preserved: q scales down by the same ratio as p
    assert after_q / after_p == pytest.approx(before_q / before_p, rel=1e-9)


def test_apply_battery_discharge_makes_p_more_negative(solved_net):
    net = copy.deepcopy(solved_net)
    idx = net.storage.index[net.storage["name"] == "hospital_bess"][0]
    before_p = float(net.storage.loc[idx, "p_mw"])
    apply_proposed_action(net, _action("batt-1", "battery_discharge", 50.0))
    after_p = float(net.storage.loc[idx, "p_mw"])
    assert after_p == pytest.approx(before_p - 0.05)


def test_apply_emergency_reserve_is_a_noop(solved_net):
    net = copy.deepcopy(solved_net)
    before = net.load.copy(), net.sgen.copy(), net.storage.copy()
    apply_proposed_action(net, _action("batt-1", "emergency_reserve", 30.0))
    pd_before_load, pd_before_sgen, pd_before_storage = before
    assert net.load.equals(pd_before_load)
    assert net.sgen.equals(pd_before_sgen)
    assert net.storage.equals(pd_before_storage)


def test_apply_unknown_asset_id_raises(solved_net):
    net = copy.deepcopy(solved_net)
    with pytest.raises(ValueError, match="unknown asset_id"):
        apply_proposed_action(net, _action("nope-1", "load_reduction", 10.0))


def test_apply_type_asset_mismatch_raises(solved_net):
    """battery_discharge pointed at a load asset is an integration bug,
    not electrical infeasibility -- should raise, not silently misapply."""
    net = copy.deepcopy(solved_net)
    with pytest.raises(ValueError):
        apply_proposed_action(net, _action("acad-1", "battery_discharge", 10.0))


# --- check_constraints ---------------------------------------------------


def test_check_constraints_empty_on_healthy_baseline(solved_net):
    assert check_constraints(solved_net) == []


def test_check_constraints_flags_overvoltage(solved_net):
    """Fully shedding academic's load pushes its bus voltage above 1.05pu
    (verified: 1.0593pu) -- a real, computed violation."""
    net = copy.deepcopy(solved_net)
    apply_proposed_action(net, _action("acad-1", "hvac_reduction", 240.0))
    run_power_flow(net)
    violations = check_constraints(net)
    assert any("academic voltage" in v for v in violations)


# --- _asset_capacity_kw ---------------------------------------------------


def test_asset_capacity_kw_battery_capped_by_rated_power(solved_net):
    """soc=72%, max_e=0.5MWh, min_e=0.05MWh -> energy-derived limit is
    ((0.72*0.5)-0.05)/(15/60) = 1240kW, well above the 250kW rated power
    cap -- so the rated power should be the binding constraint."""
    cap = _asset_capacity_kw(solved_net, _action("batt-1", "battery_discharge", 9999.0))
    assert cap == pytest.approx(250.0)


def test_asset_capacity_kw_load_capped_at_current_draw(solved_net):
    cap = _asset_capacity_kw(solved_net, _action("hosp-1", "load_reduction", 9999.0))
    assert cap == pytest.approx(380.0)


def test_asset_capacity_kw_emergency_reserve_is_uncapped(solved_net):
    assert _asset_capacity_kw(solved_net, _action("batt-1", "emergency_reserve", 30.0)) is None


def test_asset_capacity_kw_unknown_asset_raises(solved_net):
    with pytest.raises(ValueError, match="unknown asset_id"):
        _asset_capacity_kw(solved_net, _action("nope-1", "load_reduction", 10.0))


# --- validate_action -------------------------------------------------------


def test_validate_action_feasible_matches_integration_md_example(solved_net):
    """Mirrors Person 2's verified INTEGRATION.md clear_market response:
    ev_delay 14kW on ev-1, hvac_reduction 28kW on acad-1 -- both should be
    feasible against the baseline with no capping needed."""
    r_ev = validate_action(solved_net, _action("ev-1", "ev_delay", 14.0, "act-1"))
    r_acad = validate_action(solved_net, _action("acad-1", "hvac_reduction", 28.0, "act-2"))
    assert r_ev == ValidationResult(action_id="act-1", feasible=True)
    assert r_acad == ValidationResult(action_id="act-2", feasible=True)


def test_validate_action_does_not_mutate_base_net(solved_net):
    before = solved_net.res_line[["loading_percent"]].copy()
    validate_action(solved_net, _action("hosp-1", "load_reduction", 100.0))
    pd_after = solved_net.res_line[["loading_percent"]]
    assert before.equals(pd_after)


def test_validate_action_caps_oversized_battery_request(solved_net):
    result = validate_action(solved_net, _action("batt-1", "battery_discharge", 500.0))
    assert result.feasible is True
    assert result.adjusted_kw_amount == pytest.approx(250.0)
    assert "capped" in result.reason


def test_validate_action_caps_oversized_load_shed_request(solved_net):
    result = validate_action(solved_net, _action("hosp-1", "load_reduction", 480.0))
    assert result.feasible is True
    assert result.adjusted_kw_amount == pytest.approx(380.0)


def test_validate_action_rejects_action_causing_real_overvoltage(solved_net):
    result = validate_action(solved_net, _action("acad-1", "hvac_reduction", 240.0))
    assert result.feasible is False
    assert "voltage" in result.reason


def test_validate_action_unknown_asset_is_infeasible_not_a_crash(solved_net):
    result = validate_action(solved_net, _action("nope-1", "load_reduction", 10.0))
    assert result.feasible is False
    assert "unknown asset_id" in result.reason


def test_validate_action_reports_islanded_network_as_infeasible(solved_net):
    """Disconnecting the transformer islands the whole LV side. pandapower
    still converges here (it just leaves the unreachable buses/lines as
    NaN results) rather than raising LoadflowNotConverged -- confirmed by
    direct experiment. check_constraints treats NaN loading as a real
    "disconnected/islanded" violation rather than silently passing, and
    validate_action should surface that as infeasible."""
    net = copy.deepcopy(solved_net)
    net.trafo.loc[0, "in_service"] = False
    run_power_flow(net)  # converges; LV side is just islanded (NaN results)
    assert net.res_line["loading_percent"].isna().all()

    result = validate_action(net, _action("ev-1", "ev_delay", 5.0))
    assert result.feasible is False
    assert "disconnected/islanded" in result.reason


def test_validate_action_reports_nonconvergence_as_infeasible(monkeypatch, solved_net):
    """Exercise the LoadflowNotConverged except-clause directly: force
    run_power_flow (as imported into scenarios.py) to raise it, and
    confirm validate_action reports a clear infeasible reason rather than
    letting the exception propagate or treating it as "no violations"."""
    import grid_engine.scenarios as scenarios_module

    def _always_fails(net):
        raise pp.powerflow.LoadflowNotConverged("forced for test")

    monkeypatch.setattr(scenarios_module, "run_power_flow", _always_fails)
    result = validate_action(solved_net, _action("ev-1", "ev_delay", 5.0))
    assert result.feasible is False
    assert "non-convergent" in result.reason


# --- validate_actions (cumulative batch) -----------------------------------


def test_validate_actions_cumulative_does_not_mutate_base_net(solved_net):
    before = solved_net.res_line[["loading_percent"]].copy()
    validate_actions(solved_net, [
        _action("ev-1", "ev_delay", 14.0, "act-1"),
        _action("acad-1", "hvac_reduction", 28.0, "act-2"),
    ])
    assert before.equals(solved_net.res_line[["loading_percent"]])


def test_validate_actions_returns_one_result_per_action_in_order(solved_net):
    actions = [
        _action("ev-1", "ev_delay", 14.0, "act-1"),
        _action("acad-1", "hvac_reduction", 28.0, "act-2"),
        _action("batt-1", "battery_discharge", 500.0, "act-3"),
    ]
    results = validate_actions(solved_net, actions)
    assert [r.action_id for r in results] == ["act-1", "act-2", "act-3"]
    assert all(r.feasible for r in results)


def test_validate_actions_rejected_action_does_not_block_the_next_one(solved_net):
    """An infeasible action in the middle of a batch shouldn't be applied,
    but the next action should still be validated against the state as it
    stood before the rejected one (not skipped, not crashed)."""
    actions = [
        _action("acad-1", "hvac_reduction", 240.0, "act-1"),  # causes overvoltage -> rejected
        _action("ev-1", "ev_delay", 14.0, "act-2"),  # should still be evaluated normally
    ]
    results = validate_actions(solved_net, actions)
    assert results[0].feasible is False
    assert results[1].feasible is True
