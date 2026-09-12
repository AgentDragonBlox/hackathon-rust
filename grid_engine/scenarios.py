"""
scenarios.py

Milestone 5: accepts Person 2's ProposedAction objects (shared/contracts.py),
applies each to a COPY of the current grid state, reruns AC power flow, and
determines electrical feasibility -- returning a ValidationResult per the
shared contract. Never mutates the caller's live network; every check is a
real power-flow rerun, never an assumption.

Milestone 6 (simulated fault injection -- solar drop, demand spike,
outage, line fault, battery failure -- and islanding) ended up living in
its own module, grid_engine/faults.py, rather than here: it needed real
topology/connected-components analysis and a live server-side fault
registry that don't fit this module's "always operate on a plain
pandapower net, no server state" shape. See faults.py's own docstring.

-------------------------------------------------------------------------
HOW AN ACTION MAPS TO THE NETWORK
-------------------------------------------------------------------------

action.asset_id is looked up in contracts_adapter.ASSET_ID_TO_INTERNAL to
find which pandapower load/sgen/storage element it refers to.
action.action_type decides what changes:

  - "load_reduction", "hvac_reduction", "production_flex" -> reduce the
    mapped LOAD's p_mw by kw_amount (q_mvar scaled down proportionally,
    keeping the same power factor). Can't shed more than the asset's
    current load -- kw_amount is capped at that, with the cap reported
    back via ValidationResult.adjusted_kw_amount.
  - "ev_delay" -> same effect as the load-reduction types above (deferring
    the charging session reduces right-now demand the same way shedding
    would; grid_engine doesn't need to know it's deferred rather than
    cancelled).
  - "battery_discharge" -> makes the mapped storage element's p_mw more
    negative (pandapower convention: negative p_mw = discharging/
    supplying). Capped at the smaller of the battery's rated max_p_mw and
    an energy-derived limit (see _asset_capacity_kw below) -- both are
    real physical limits, not grid-congestion limits.
  - "emergency_reserve" -> intentionally a NO-OP here. Per Person 2's
    INTEGRATION.md, this represents a standing capacity commitment
    (ReserveContract), not an immediate dispatch -- nothing in the current
    pipeline calls it with an expectation of an instantaneous power
    change, so validating it just means "yes, this doesn't break
    anything" against the unmodified state.

-------------------------------------------------------------------------
WHAT "FEASIBLE" MEANS HERE
-------------------------------------------------------------------------

After applying an action (capped at the asset's own physical limits if
needed) to a copy of the network, power flow is rerun and checked against:
  - every feeder (F1/F2/F3) and the transformer: loading_percent <= 100%
  - every bus: voltage within [0.95, 1.05] pu (same band as grid.py's
    Milestone-1 healthy-operating threshold)

A non-convergent power flow is reported as infeasible with a clear reason,
not silently treated as "no violations found".
"""

import copy

import pandas as pd
import pandapower as pp

from grid_engine.contracts_adapter import ASSET_ID_TO_INTERNAL
from grid_engine.grid import get_asset_indices, run_power_flow
from shared.contracts import ProposedAction, ValidationResult

LOADING_LIMIT_PCT = 100.0
MIN_VM_PU = 0.95
MAX_VM_PU = 1.05

# Action types that reduce a LOAD element (vs. the battery-discharge /
# emergency-reserve special cases handled separately below).
LOAD_SHEDDING_ACTION_TYPES = {"load_reduction", "hvac_reduction", "production_flex", "ev_delay"}

# ASSUMED sustain duration used to convert the battery's available energy
# (above its reserve floor) into a kW cap -- matches forecasting.py's
# default 15-minute forecast checkpoint. Not specified anywhere in
# shared/contracts.py; flagged here rather than left as a silent magic
# number.
ASSUMED_DISCHARGE_SUSTAIN_HOURS = 15.0 / 60.0


def apply_proposed_action(net: pp.pandapowerNet, action: ProposedAction) -> None:
    """
    Apply one ProposedAction to `net` IN PLACE. Callers that don't want
    the original network touched must pass a copy (see validate_action,
    which does this for you).

    Raises ValueError for an unrecognized asset_id or a type/asset
    mismatch (e.g. a battery_discharge action pointed at a load asset) --
    these are integration bugs on the caller's side, not electrical
    infeasibility, so they're raised rather than turned into a
    ValidationResult(feasible=False).
    """
    if action.asset_id not in ASSET_ID_TO_INTERNAL:
        raise ValueError(
            f"unknown asset_id {action.asset_id!r} -- not in "
            f"contracts_adapter.ASSET_ID_TO_INTERNAL"
        )
    table, internal_name = ASSET_ID_TO_INTERNAL[action.asset_id]
    indices = get_asset_indices(net)
    if internal_name not in indices:
        raise ValueError(f"asset {internal_name!r} not found in this network")
    _, idx = indices[internal_name]

    if action.action_type == "battery_discharge":
        if table != "storage":
            raise ValueError(
                f"battery_discharge action targets asset_id {action.asset_id!r}, "
                f"which maps to a {table!r} element, not storage"
            )
        current_p_mw = net.storage.loc[idx, "p_mw"]
        net.storage.loc[idx, "p_mw"] = current_p_mw - (action.kw_amount / 1000.0)

    elif action.action_type == "emergency_reserve":
        pass  # standing commitment, not an immediate dispatch -- see module docstring

    elif action.action_type in LOAD_SHEDDING_ACTION_TYPES or table == "load":
        if table != "load":
            raise ValueError(
                f"{action.action_type!r} action targets asset_id {action.asset_id!r}, "
                f"which maps to a {table!r} element, not a load"
            )
        current_p_mw = float(net.load.loc[idx, "p_mw"])
        current_q_mvar = float(net.load.loc[idx, "q_mvar"])
        reduction_mw = min(action.kw_amount / 1000.0, current_p_mw)
        scale = (current_p_mw - reduction_mw) / current_p_mw if current_p_mw > 0 else 1.0
        net.load.loc[idx, "p_mw"] = current_p_mw - reduction_mw
        net.load.loc[idx, "q_mvar"] = current_q_mvar * scale  # keep the same power factor

    else:
        raise ValueError(f"unrecognized action_type {action.action_type!r}")


def check_constraints(
    net: pp.pandapowerNet,
    loading_limit_pct: float = LOADING_LIMIT_PCT,
    min_vm_pu: float = MIN_VM_PU,
    max_vm_pu: float = MAX_VM_PU,
) -> list[str]:
    """
    Return a list of human-readable violation descriptions for a network
    that has already had power flow run on it. Empty list = no violations.
    """
    violations = []

    for idx, line_row in net.line.iterrows():
        loading = net.res_line.loc[idx, "loading_percent"]
        if pd.isna(loading):
            violations.append(f"line {line_row['name']} has no power-flow result (disconnected/islanded)")
        elif loading > loading_limit_pct:
            violations.append(f"line {line_row['name']} loading {loading:.1f}% exceeds {loading_limit_pct:.0f}%")

    trafo_loading = net.res_trafo["loading_percent"].iloc[0]
    if pd.isna(trafo_loading):
        violations.append("transformer has no power-flow result (disconnected/islanded)")
    elif trafo_loading > loading_limit_pct:
        violations.append(f"transformer loading {trafo_loading:.1f}% exceeds {loading_limit_pct:.0f}%")

    for idx, bus_row in net.bus.iterrows():
        vm = net.res_bus.loc[idx, "vm_pu"]
        if pd.isna(vm):
            continue  # already reported via the line/transformer checks above
        if vm < min_vm_pu or vm > max_vm_pu:
            violations.append(
                f"bus {bus_row['name']} voltage {vm:.4f} pu outside [{min_vm_pu}, {max_vm_pu}] pu"
            )

    return violations


def _asset_capacity_kw(net: pp.pandapowerNet, action: ProposedAction) -> float | None:
    """
    The asset's own physical delivery limit for this action, in kW --
    independent of grid congestion. Returns None when there's no
    meaningful cap to apply (emergency_reserve).

    Raises ValueError for an unknown/missing asset_id -- mirrors the same
    check in apply_proposed_action, since this runs first in
    validate_action and needs to fail the same clean way.
    """
    if action.asset_id not in ASSET_ID_TO_INTERNAL:
        raise ValueError(
            f"unknown asset_id {action.asset_id!r} -- not in "
            f"contracts_adapter.ASSET_ID_TO_INTERNAL"
        )
    table, internal_name = ASSET_ID_TO_INTERNAL[action.asset_id]
    indices = get_asset_indices(net)
    if internal_name not in indices:
        raise ValueError(f"asset {internal_name!r} not found in this network")
    _, idx = indices[internal_name]

    if action.action_type == "battery_discharge":
        max_p_mw = net.storage.loc[idx, "max_p_mw"]
        rate_limit_kw = float(max_p_mw) * 1000.0 if pd.notna(max_p_mw) else None

        soc_percent = net.storage.loc[idx, "soc_percent"]
        min_e_mwh = float(net.storage.loc[idx, "min_e_mwh"])
        max_e_mwh = float(net.storage.loc[idx, "max_e_mwh"])
        available_e_mwh = max(0.0, (float(soc_percent) / 100.0) * max_e_mwh - min_e_mwh)
        energy_limit_kw = (available_e_mwh / ASSUMED_DISCHARGE_SUSTAIN_HOURS) * 1000.0

        caps = [c for c in (rate_limit_kw, energy_limit_kw) if c is not None]
        return min(caps) if caps else None

    if action.action_type == "emergency_reserve":
        return None

    # load-shedding-style actions: can't shed more than the asset is
    # currently drawing.
    current_p_mw = float(net.load.loc[idx, "p_mw"])
    return current_p_mw * 1000.0


def validate_action(base_net: pp.pandapowerNet, action: ProposedAction) -> ValidationResult:
    """
    Validate one ProposedAction against `base_net` (NOT mutated -- a copy
    is used internally). Returns a ValidationResult per shared/contracts.py.
    """
    try:
        cap_kw = _asset_capacity_kw(base_net, action)
    except ValueError as exc:
        return ValidationResult(action_id=action.action_id, feasible=False, reason=str(exc))
    requested_kw = action.kw_amount
    was_capped = cap_kw is not None and requested_kw > cap_kw
    effective_kw = min(requested_kw, max(cap_kw, 0.0)) if cap_kw is not None else requested_kw

    candidate = copy.deepcopy(base_net)
    effective_action = action.model_copy(update={"kw_amount": effective_kw})
    try:
        apply_proposed_action(candidate, effective_action)
        run_power_flow(candidate)
    except pp.powerflow.LoadflowNotConverged:
        return ValidationResult(
            action_id=action.action_id, feasible=False,
            reason="proposed action leads to a non-convergent power flow",
        )
    except ValueError as exc:
        return ValidationResult(action_id=action.action_id, feasible=False, reason=str(exc))

    violations = check_constraints(candidate)
    adjusted = round(effective_kw, 1) if was_capped else None

    if violations:
        reason = "; ".join(violations)
        if was_capped:
            reason = f"even capped at {effective_kw:.1f} kW (asset limit): {reason}"
        return ValidationResult(
            action_id=action.action_id, feasible=False, reason=reason, adjusted_kw_amount=adjusted,
        )

    if was_capped:
        return ValidationResult(
            action_id=action.action_id, feasible=True,
            reason=f"capped at asset's available capacity: {effective_kw:.1f} kW of {requested_kw:.1f} kW requested",
            adjusted_kw_amount=adjusted,
        )
    return ValidationResult(action_id=action.action_id, feasible=True)


def validate_actions(base_net: pp.pandapowerNet, actions: list[ProposedAction]) -> list[ValidationResult]:
    """
    Validate a list of proposed actions CUMULATIVELY: each is validated
    against the state left behind by the previously-accepted ones (as they
    would actually be if settled in this order), not independently against
    the same unmodified base state. `base_net` itself is never mutated.
    Order matters -- pass actions in the order they'd actually be settled.
    Infeasible actions are not applied; the next action is still validated
    against the state as it stood before the rejected one.
    """
    working_net = copy.deepcopy(base_net)
    results = []
    for action in actions:
        result = validate_action(working_net, action)
        results.append(result)
        if result.feasible:
            applied_kw = result.adjusted_kw_amount if result.adjusted_kw_amount is not None else action.kw_amount
            applied_action = action.model_copy(update={"kw_amount": applied_kw})
            apply_proposed_action(working_net, applied_action)
            run_power_flow(working_net)
    return results
