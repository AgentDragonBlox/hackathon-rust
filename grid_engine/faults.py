"""
faults.py

Milestone 6: simulated fault injection, per shared/contracts.py's
FaultInjectionRequest. Every effect here is either a direct physical
change to a pandapower element's in_service flag, or a documented
ASSUMED magnitude -- and every resulting GridState is built from a real
rerun (or, for grid_outage, a real *attempted* rerun -- see below), never
assumed to "just work." Same philosophy as scenarios.py: calculate,
don't fabricate.

This module is deliberately stateless -- every function takes a network
and mutates or inspects it directly. The live fault registry (which
faults are currently active, and rebuilding the served network from a
pristine baseline whenever that set changes) lives in grid_engine/api.py,
since that's already where this project's one piece of real server-side
state (`_net`) lives, and its own docstring already documents the
state model.

-------------------------------------------------------------------------
FLAGGED: FaultInjectionRequest is filed under Person 2's section of
shared/contracts.py ("COMPOSITE REQUEST/RESPONSE SHAPES FOR THE PERSON 2
ENDPOINTS ... wire shapes for /agents/offers"), but every fault_type here
(solar_drop, demand_spike, feeder_overload, battery_failure, grid_outage,
line_fault) is a physical grid event -- squarely grid_engine's domain.
Not editing Person 2's comment in the shared file unilaterally (see
contracts.py's own "don't fork copies" rule at the top) -- flagging it
here and in grid_engine/INTEGRATION.md instead. Treating grid_engine as
the owner of POST /grid/fault since nothing in agent_engines'
INTEGRATION.md claims a route for this.

-------------------------------------------------------------------------
FLAGGED: FaultInjectionRequest has no magnitude field (how much solar
drops, how big a demand spike) and no asset_id (which specific line/
battery within a feeder, for a feeder with more than one). The ASSUMED
constants below fill that gap -- flag to the team if a real value gets
agreed on instead of an assumption made on this side.

-------------------------------------------------------------------------
WHICH feeder_id IS VALID FOR EACH fault_type
-------------------------------------------------------------------------
line_fault, demand_spike, feeder_overload -> any of "F1", "F2", "F3"
  (the feeder's own line is taken out / its loads scaled up).
solar_drop      -> "F2" only -- solar-1 is the only sgen, and it's on F2.
battery_failure -> "F1" only -- batt-1 is the only storage, and it's on F1.
grid_outage     -> feeder_id is REQUIRED by the shared schema but not
  actually used by this fault type -- it disconnects the ext_grid, which
  affects the whole campus, not one feeder. Flagged as a real schema
  mismatch, not silently worked around: any of "F1"/"F2"/"F3" is accepted
  and has the identical whole-grid effect.
An unrecognized feeder_id/fault_type combination raises
FaultApplicationError with a clear reason -- an integration bug on the
caller's side, not an electrical result, same distinction scenarios.py
draws for apply_proposed_action.
"""

import copy

import pandapower as pp
import pandapower.topology as top
import networkx as nx

from grid_engine.contracts_adapter import ASSET_ID_TO_INTERNAL, FEEDER_ASSET_IDS, INTERNAL_TO_ASSET_ID
from grid_engine.grid import get_asset_indices, run_power_flow

VALID_FEEDER_IDS = {"F1", "F2", "F3"}

# ASSUMED magnitudes -- not specified anywhere in shared/contracts.py.
ASSUMED_SOLAR_DROP_REMAINING_FRACTION = 0.15  # e.g. heavy cloud cover / partial panel fault -- an 85% drop
ASSUMED_DEMAND_SPIKE_FRACTION = 0.40  # +40% of the feeder's current total load

# feeder_overload searches (via real power-flow reruns) for the smallest
# load-scale factor that pushes the feeder's line loading to just past
# this target, capped at this many multiples of current load. AC power
# flow can stop converging well before an arbitrarily large scale (found
# by direct experiment: F1 scaled to 5x doesn't converge at all, even
# though ~1.5x already clears the target) -- so the search is a forward
# scan for the first (scale, converges, over-target) point, THEN a
# binary-search refinement between that and the last known
# still-converging-but-under-target point, same two-phase style as
# forecasting.py's compute_required_relief_kw.
FEEDER_OVERLOAD_TARGET_PCT = 105.0
FEEDER_OVERLOAD_MAX_SCALE = 5.0
FEEDER_OVERLOAD_SCAN_STEPS = 40
FEEDER_OVERLOAD_REFINE_ITERATIONS = 15


class FaultApplicationError(ValueError):
    """Raised for an invalid fault_type/feeder_id combination -- an
    integration bug on the caller's side, not an electrical result."""


def _feeder_line_idx(net: pp.pandapowerNet, feeder_id: str):
    matches = net.line.index[net.line["name"] == feeder_id]
    if len(matches) == 0:
        raise FaultApplicationError(f"unknown feeder_id {feeder_id!r} -- not a line in this network")
    return matches[0]


def _scale_feeder_loads(net: pp.pandapowerNet, feeder_id: str, factor: float) -> None:
    """Scale every LOAD asset on `feeder_id` by `factor` (multiplicative,
    applied to both p_mw and q_mvar so power factor is preserved). Skips
    non-load assets on the same feeder (e.g. F2's solar-1) -- a demand
    spike is a load-side event."""
    indices = get_asset_indices(net)
    scaled_any = False
    for asset_id in FEEDER_ASSET_IDS.get(feeder_id, []):
        table, internal_name = ASSET_ID_TO_INTERNAL[asset_id]
        if table != "load" or internal_name not in indices:
            continue
        _, idx = indices[internal_name]
        net.load.loc[idx, "p_mw"] = float(net.load.loc[idx, "p_mw"]) * factor
        net.load.loc[idx, "q_mvar"] = float(net.load.loc[idx, "q_mvar"]) * factor
        scaled_any = True
    if not scaled_any:
        raise FaultApplicationError(f"feeder {feeder_id!r} has no load assets to scale")


def _induce_feeder_overload(net: pp.pandapowerNet, feeder_id: str) -> None:
    """Find the smallest load-scale factor (within FEEDER_OVERLOAD_MAX_SCALE)
    that pushes `feeder_id`'s real, rerun line loading past
    FEEDER_OVERLOAD_TARGET_PCT, then apply it to `net`. Every candidate
    scale is verified with an actual power-flow rerun on a throwaway
    copy -- never assumed from a linear guess. Two phases: a forward scan
    (AC power flow can stop converging well before an arbitrarily large
    scale, so the search can't just binary-search top-down from
    FEEDER_OVERLOAD_MAX_SCALE) to find any (converges, over-target) point,
    then a binary-search refinement for a tighter value."""
    line_idx = _feeder_line_idx(net, feeder_id)

    def loading_at(scale: float) -> float | None:
        candidate = copy.deepcopy(net)
        _scale_feeder_loads(candidate, feeder_id, scale)
        try:
            run_power_flow(candidate)
        except pp.powerflow.LoadflowNotConverged:
            return None
        return float(candidate.res_line.loc[line_idx, "loading_percent"])

    step = (FEEDER_OVERLOAD_MAX_SCALE - 1.0) / FEEDER_OVERLOAD_SCAN_STEPS
    lo = 1.0  # last scale confirmed to converge but stay under target
    hi = None  # first scale confirmed to converge AND reach target
    scale = 1.0 + step
    while scale <= FEEDER_OVERLOAD_MAX_SCALE + 1e-9:
        loading = loading_at(scale)
        if loading is not None and loading >= FEEDER_OVERLOAD_TARGET_PCT:
            hi = scale
            break
        if loading is not None:
            lo = scale
        scale += step

    if hi is None:
        raise FaultApplicationError(
            f"could not induce a real overload on {feeder_id} within an assumed "
            f"{FEEDER_OVERLOAD_MAX_SCALE}x load-scale scan (last converging point "
            f"reached only {lo}x scale) -- ASSUMED_DEMAND_SPIKE_FRACTION-style "
            f"constants may need revisiting for this feeder"
        )

    for _ in range(FEEDER_OVERLOAD_REFINE_ITERATIONS):
        mid = (lo + hi) / 2.0
        loading = loading_at(mid)
        if loading is not None and loading >= FEEDER_OVERLOAD_TARGET_PCT:
            hi = mid
        else:
            lo = mid
    _scale_feeder_loads(net, feeder_id, hi)


def apply_fault(net: pp.pandapowerNet, feeder_id: str, fault_type: str) -> None:
    """
    Apply one fault to `net` IN PLACE, per FaultInjectionRequest's
    (feeder_id, fault_type). Callers that don't want a shared/baseline
    network touched must pass a copy -- see api.py's rebuild-from-
    baseline state model, which always does this.
    """
    if fault_type == "line_fault":
        if feeder_id not in VALID_FEEDER_IDS:
            raise FaultApplicationError(f"line_fault requires feeder_id in {sorted(VALID_FEEDER_IDS)}, got {feeder_id!r}")
        idx = _feeder_line_idx(net, feeder_id)
        net.line.loc[idx, "in_service"] = False

    elif fault_type == "grid_outage":
        # feeder_id is required by the shared schema but not used here -- see module docstring.
        if feeder_id not in VALID_FEEDER_IDS:
            raise FaultApplicationError(f"grid_outage requires feeder_id in {sorted(VALID_FEEDER_IDS)} (value unused), got {feeder_id!r}")
        net.ext_grid.loc[0, "in_service"] = False

    elif fault_type == "battery_failure":
        if feeder_id != "F1":
            raise FaultApplicationError(f"battery_failure requires feeder_id='F1' (batt-1's feeder), got {feeder_id!r}")
        indices = get_asset_indices(net)
        _, idx = indices["hospital_bess"]
        net.storage.loc[idx, "in_service"] = False

    elif fault_type == "solar_drop":
        if feeder_id != "F2":
            raise FaultApplicationError(f"solar_drop requires feeder_id='F2' (solar-1's feeder), got {feeder_id!r}")
        indices = get_asset_indices(net)
        _, idx = indices["solar"]
        current_p_mw = float(net.sgen.loc[idx, "p_mw"])
        net.sgen.loc[idx, "p_mw"] = current_p_mw * ASSUMED_SOLAR_DROP_REMAINING_FRACTION

    elif fault_type == "demand_spike":
        if feeder_id not in VALID_FEEDER_IDS:
            raise FaultApplicationError(f"demand_spike requires feeder_id in {sorted(VALID_FEEDER_IDS)}, got {feeder_id!r}")
        _scale_feeder_loads(net, feeder_id, 1.0 + ASSUMED_DEMAND_SPIKE_FRACTION)

    elif fault_type == "feeder_overload":
        if feeder_id not in VALID_FEEDER_IDS:
            raise FaultApplicationError(f"feeder_overload requires feeder_id in {sorted(VALID_FEEDER_IDS)}, got {feeder_id!r}")
        _induce_feeder_overload(net, feeder_id)

    else:
        raise FaultApplicationError(f"unrecognized fault_type {fault_type!r}")


def compute_islands(net: pp.pandapowerNet) -> list[list[str]]:
    """
    Real connected-components analysis of `net`'s actual topology
    (pandapower's own graph, respecting in_service on lines/trafo/
    switches) -- returns groups of asset_ids that are NOT connected to
    the ext_grid's bus. [] when nothing is islanded. Computed from
    topology, never inferred from loading/voltage results.

    Special case: if the ext_grid itself is out of service (grid_outage),
    there is no reference bus anywhere in the network, so the entire
    campus is one island regardless of line connectivity -- reported as
    a single group of every known asset_id.
    """
    if len(net.ext_grid) == 0 or not bool(net.ext_grid.loc[0, "in_service"]):
        all_ids = sorted(INTERNAL_TO_ASSET_ID.values())
        return [all_ids] if all_ids else []

    graph = top.create_nxgraph(net, respect_switches=True)
    ext_grid_bus = int(net.ext_grid.loc[0, "bus"])

    islands = []
    for component in nx.connected_components(graph):
        if ext_grid_bus in component:
            continue  # the main, still-energized island
        asset_ids = _asset_ids_on_buses(net, component)
        if asset_ids:
            islands.append(sorted(asset_ids))
    return islands


def _asset_ids_on_buses(net: pp.pandapowerNet, bus_indices) -> list[str]:
    asset_ids = []
    for table_name in ("load", "sgen", "storage"):
        table = getattr(net, table_name)
        for _, row in table.iterrows():
            if row["bus"] in bus_indices:
                asset_id = INTERNAL_TO_ASSET_ID.get(row["name"])
                if asset_id:
                    asset_ids.append(asset_id)
    return asset_ids
