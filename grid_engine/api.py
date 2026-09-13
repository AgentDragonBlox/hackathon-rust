"""
api.py

A thin FastAPI wrapper around grid_engine's real pandapower logic, so
Person 3's orchestrator can call grid_engine over HTTP the same way it
calls Person 2's agent_engines service (see grid_engine/INTEGRATION.md
for the full write-up, verified example payloads, and known gaps -- this
module intentionally has almost no logic of its own; it just adapts
scenarios.py / contracts_adapter.py / faults.py to a handful of routes).

Run it:
    uvicorn grid_engine.api:app --port 8001 --reload

Verify it's up:
    curl http://localhost:8001/health -> {"status":"ok"}

-------------------------------------------------------------------------
ROUTES
-------------------------------------------------------------------------

GET  /health              -> {"status": "ok"}
GET  /grid/state           -> GridState (current LIVE network -- see STATE MODEL)
POST /grid/validate        -> ValidateActionsRequest {"actions": [...]}
                               -> list[ValidationResult]  (non-mutating)
POST /grid/apply            -> ApplyActionsRequest {"actions": [...]}
                               -> list[ApplyResult]  (MUTATING -- see STATE MODEL and
                               post_apply_actions' own docstring)
GET  /grid/applied_actions  -> informational audit trail, not part of the shared contract
POST /grid/fault            -> FaultInjectionRequest {"feeder_id", "fault_type"}
                               -> GridState (after injecting; mutating -- see STATE MODEL)
POST /grid/fault/clear      -> ClearFaultRequest {"feeder_id"}
                               -> GridState (after clearing that one fault)
POST /grid/fault/clear_all  -> (no body) -> GridState (back to the pristine baseline)

-------------------------------------------------------------------------
STATE MODEL -- READ THIS BEFORE WIRING THE ORCHESTRATOR
-------------------------------------------------------------------------

This process holds a pristine baseline network (`_baseline_net`, built
once at import time from grid.build_campus_network() and solved once)
and a LIVE network (`_net`) that GET /grid/state actually describes.
/grid/validate is still deliberately non-mutating -- it validates against
`_net` via scenarios.validate_actions(), which works on its own deep copy
and never touches `_net` (see scenarios.py's own docstring).

Milestone 7 closes the gap earlier milestones flagged: POST /grid/apply
DOES commit an already-validated, already-settled action into `_net`,
reusing scenarios.apply_proposed_action so the applied physics matches
exactly what validation tested. See post_apply_actions' own docstring and
grid_engine/applied_state.py for the full mechanism (idempotency guard,
persisted battery energy). The orchestrator calls this once per tick,
after settlement succeeds, with exactly the actions that were settled --
never on rejected or unsettled ones.

Milestone 6 adds the one part of this service that IS stateful and
mutating: fault injection. `_active_faults` is a live registry (feeder_id
-> (feeder_id, fault_type), see faults.py for why the key isn't always
literally the request's feeder_id) of every fault currently "in effect."
POST /grid/fault adds one; POST /grid/fault/clear(_all) removes one/all.
Every change rebuilds `_net` FROM `_baseline_net`, replaying every
still-active fault via faults.apply_fault() -- never by patching `_net`
incrementally -- so clearing one fault can never leave a stray mutation
behind from a different one, and the result is always a real, freshly
rerun power flow (or, for grid_outage, a real attempted rerun -- see
faults.py and contracts_adapter._safe_kw for why that fault type has no
solvable power flow at all, and how that's represented without
fabricating a number).

GET /grid/state after an injected fault reflects it and keeps reflecting
it until cleared -- this is what lets a dashboard show an ongoing
degraded/islanded state rather than a one-off computed result.
"""

import copy
import os
from functools import wraps
from threading import RLock
from typing import Literal

import pandapower as pp
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from grid_engine.applied_state import AppliedState
from grid_engine.contracts_adapter import ASSET_ID_TO_INTERNAL, build_grid_state
from grid_engine.faults import FaultApplicationError, apply_fault, compute_islands
from grid_engine.grid import build_campus_network, run_power_flow, get_asset_indices
from grid_engine.simulation import apply_profile_row
from grid_engine.replay import PublicReplay
from grid_engine.scenarios import ASSUMED_DISCHARGE_SUSTAIN_HOURS, apply_proposed_action, validate_actions
from shared.contracts import (
    ApplyActionsRequest,
    ApplyResult,
    ClearFaultRequest,
    FaultInjectionRequest,
    GridState,
    ValidateActionsRequest,
    ValidationResult,
)

app = FastAPI(title="Grid Engine API")

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # fine for local dev; tighten before any real deployment
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pristine baseline -- never mutated after this point. Every rebuild of
# `_net` starts from a deep copy of this, never from `_net` itself.
_baseline_net = build_campus_network()
run_power_flow(_baseline_net)

# Live fault registry: registry key -> (feeder_id_submitted, fault_type).
# Key is normally the feeder_id; for grid_outage it's the synthetic
# "GRID" (see _registry_key below and faults.py's module docstring for
# why grid_outage isn't really feeder-scoped despite the shared schema
# requiring a feeder_id for it). Keying by feeder_id means injecting a
# new fault_type on a feeder that already has one active REPLACES it
# rather than stacking two faults on the same feeder -- matches
# GridState.active_faults being feeder-id-keyed per contracts.py's own
# comment ("feeder_ids currently faulted").
_active_faults: dict[str, tuple[str, str]] = {}
_net = copy.deepcopy(_baseline_net)
_data_mode = os.environ.get("GRID_DATA_MODE", "public")
if _data_mode not in ("public", "baseline"):
    raise ValueError("GRID_DATA_MODE must be public or baseline")
_replay = PublicReplay() if _data_mode == "public" else None
_state_lock = RLock()

# Milestone 7: physical feedback loop state -- see applied_state.py's
# module docstring. Battery energy is seeded from the baseline network's
# own soc_percent/max_e_mwh so the very first tick already agrees with
# what GET /grid/state reported before this feature existed.
_applied = AppliedState()


def _seed_battery_energy_from_baseline() -> None:
    """(Re-)populate _applied.battery_energy_mwh from `_baseline_net`'s own
    soc_percent/max_e_mwh. Called once at import time, and again every time
    _applied.reset() is called -- reset() itself only empties the dict (it
    has no pandapower/baseline knowledge, see applied_state.py), so without
    this re-seed a lookup right after a reset would find nothing tracked
    and silently fall back to the wrong default. Callers must call this
    immediately after every _applied.reset()."""
    for idx, row in _baseline_net.storage.iterrows():
        _applied.battery_energy_mwh[row["name"]] = (float(row["soc_percent"]) / 100.0) * float(row["max_e_mwh"])


_seed_battery_energy_from_baseline()


def _apply_persisted_battery_energy(net: pp.pandapowerNet) -> None:
    """Overwrite each storage element's soc_percent in a freshly-rebuilt
    `net` with the persisted energy state, so a discharge settled on a
    previous tick is still reflected after this tick's full rebuild from
    `_baseline_net` (which always carries the ORIGINAL baseline SOC)."""
    for idx, row in net.storage.iterrows():
        name = row["name"]
        if name not in _applied.battery_energy_mwh:
            continue
        max_e_mwh = float(row["max_e_mwh"])
        energy_mwh = _applied.battery_energy_mwh[name]
        net.storage.loc[idx, "soc_percent"] = (energy_mwh / max_e_mwh * 100.0) if max_e_mwh else 0.0


def synchronized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _state_lock:
            return function(*args, **kwargs)
    return wrapped


def _registry_key(feeder_id: str, fault_type: str) -> str:
    return "GRID" if fault_type == "grid_outage" else feeder_id


def _rebuild_live_state() -> None:
    """Recompute `_net` from `_baseline_net` plus every currently active
    fault. Always a full rebuild, never an incremental patch.

    Always actually CALLS run_power_flow, even when grid_outage has
    disabled the ext_grid and the call is certain to fail -- an earlier
    version of this function skipped the call in that case as a "why
    bother" optimization, and that was a real bug, not a harmless
    shortcut: `net` here is a deep copy of `_baseline_net`, which already
    has its OWN res_* tables populated from its own successful solve.
    Skipping run_power_flow left those stale baseline values sitting in
    the copy, so GET /grid/state kept reporting normal loading numbers
    for a network that had no power flowing through it at all -- caught
    by direct testing, not theoretical. pandapower's runpp() clears every
    res_* table to NaN itself when it can't solve (confirmed by direct
    experiment for both failure modes below), which is exactly the reset
    this needs, so the fix is simply: always call it, always let it fail
    honestly."""
    global _net
    net = copy.deepcopy(_baseline_net)
    _apply_persisted_battery_energy(net)
    if _replay is not None:
        apply_profile_row(net, _replay.sample["campus_kw"], get_asset_indices(net))
    for feeder_id, fault_type in _active_faults.values():
        apply_fault(net, feeder_id, fault_type)

    try:
        run_power_flow(net)
    except pp.powerflow.LoadflowNotConverged:
        # Leaves every res_* table as NaN -- contracts_adapter._safe_kw
        # turns that into a physically honest 0.0. The network is still
        # topologically fully connected here (compute_islands() will
        # correctly report no islands), it just couldn't be numerically
        # solved. Every feeder's NetworkState.status will still read
        # "islanded" in this case (that's what a NaN loading value maps
        # to -- see contracts_adapter._line_status), which is a slight
        # overload of that enum value: it now means either "genuinely
        # topologically disconnected" or "no valid power flow could be
        # computed for some other reason." Flagged, not silently
        # resolved -- contracts.py's NetworkState.status enum has no
        # separate value for the second case.
        pass
    except UserWarning:
        # grid_outage: no reference bus anywhere in the network (ext_grid
        # disabled), so AC power flow has no solution at all -- pandapower
        # raises UserWarning("No reference bus is available...") here
        # rather than LoadflowNotConverged, but still clears every res_*
        # table to NaN the same way (confirmed by direct experiment).
        # Same _safe_kw handling applies; compute_islands() has its own
        # explicit ext_grid.in_service check, so it correctly reports the
        # whole campus as one island here rather than "none".
        pass
    _net = net


def _current_grid_state() -> GridState:
    return build_grid_state(_net, active_faults=sorted(_active_faults.keys()), islands=compute_islands(_net))


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/grid/state", response_model=GridState)
@synchronized
def get_grid_state() -> GridState:
    return _current_grid_state()


@app.post("/grid/validate", response_model=list[ValidationResult])
@synchronized
def post_validate(request: ValidateActionsRequest) -> list[ValidationResult]:
    return validate_actions(_net, request.actions)


@app.post("/grid/apply", response_model=list[ApplyResult])
@synchronized
def post_apply_actions(request: ApplyActionsRequest) -> list[ApplyResult]:
    """
    Milestone 7 -- the physical feedback loop. Commits already-validated,
    already-settled actions into the LIVE network `_net` (mutating, unlike
    /grid/validate). The orchestrator is expected to call this only with
    the subset of actions that both passed /grid/validate AND were
    successfully settled by Rust's /agents/settle -- this endpoint trusts
    that ordering the same way /agents/settle trusts its own caller (see
    README section 8); it does not re-run feasibility checks itself.

    Idempotent per action_id: an action_id already committed in a
    previous call is reported back as applied=False with a reason,
    never re-dispatched -- this is what actually prevents a duplicate
    settlement (README section 8's flagged gap: "Calling settlement
    twice can create new trade IDs") from double-affecting physical state.

    Effects, reusing scenarios.apply_proposed_action (the exact function
    /grid/validate already uses to test feasibility) so the applied
    physics is identical to what was validated:
      - load_reduction / hvac_reduction / ev_delay / production_flex:
        reduces the mapped load's p_mw/q_mvar for THIS tick only -- the
        next tick's rebuild starts from a fresh recorded profile row (see
        applied_state.py's docstring for why curtailment is not carried
        forward).
      - battery_discharge: reduces the mapped storage element's p_mw for
        this tick AND permanently debits its persisted usable energy
        (applied_state.AppliedState.battery_energy_mwh) by
        kw_amount * ASSUMED_DISCHARGE_SUSTAIN_HOURS, floored at the
        battery's own min_e_mwh reserve -- this is what makes SOC actually
        deplete across ticks, closing README's "SOC is not depleted by
        recorded settlements" gap.
      - emergency_reserve: a no-op here too, matching apply_proposed_action.

    A rejected/never-validated action must never reach this endpoint in
    the first place; nothing here can make an unapplied action "count."
    """
    results: list[ApplyResult] = []
    any_applied = False

    for action in request.actions:
        if action.action_id in _applied.applied_action_ids:
            results.append(ApplyResult(
                action_id=action.action_id, applied=False,
                reason="action_id already applied -- settlement idempotency guard",
            ))
            continue

        try:
            apply_proposed_action(_net, action)
        except ValueError as exc:
            results.append(ApplyResult(action_id=action.action_id, applied=False, reason=str(exc)))
            continue

        if action.action_type == "battery_discharge":
            _, internal_name = ASSET_ID_TO_INTERNAL[action.asset_id]
            idx = get_asset_indices(_net)[internal_name][1]
            min_e_mwh = float(_net.storage.loc[idx, "min_e_mwh"])
            max_e_mwh = float(_net.storage.loc[idx, "max_e_mwh"])
            current_mwh = _applied.battery_energy_mwh.get(internal_name, max_e_mwh)
            discharged_mwh = (action.kw_amount / 1000.0) * ASSUMED_DISCHARGE_SUSTAIN_HOURS
            new_mwh = max(min_e_mwh, current_mwh - discharged_mwh)
            _applied.battery_energy_mwh[internal_name] = new_mwh
            # Reflect the new SOC immediately too, not just on the next
            # full rebuild, so a get_state() called right after this
            # request already shows the depleted battery.
            _net.storage.loc[idx, "soc_percent"] = (new_mwh / max_e_mwh * 100.0) if max_e_mwh else 0.0

        _applied.applied_action_ids.add(action.action_id)
        _applied.log.append({
            "action_id": action.action_id,
            "asset_id": action.asset_id,
            "action_type": action.action_type,
            "kw_amount": action.kw_amount,
        })
        results.append(ApplyResult(action_id=action.action_id, applied=True))
        any_applied = True

    if any_applied:
        try:
            run_power_flow(_net)
        except pp.powerflow.LoadflowNotConverged:
            # Same honest-NaN handling as _rebuild_live_state: an applied
            # action that makes the network non-convergent is still a real
            # outcome, not something to paper over.
            pass

    return results


@app.get("/grid/applied_actions")
@synchronized
def get_applied_actions() -> dict:
    """Audit trail for tests/metrics/dashboard: every action_id actually
    committed to the live network since the last reset, plus current
    persisted battery energy. Not part of the shared wire contract --
    informational only."""
    return {
        "applied_action_ids": sorted(_applied.applied_action_ids),
        "battery_energy_mwh": dict(_applied.battery_energy_mwh),
        "log": list(_applied.log),
    }


@app.post("/grid/fault", response_model=GridState)
@synchronized
def post_inject_fault(request: FaultInjectionRequest) -> GridState:
    # Validate against a throwaway copy of the baseline first, so a bad
    # feeder_id/fault_type combination never gets registered as "active"
    # only to be discovered on rebuild.
    probe = copy.deepcopy(_baseline_net)
    try:
        apply_fault(probe, request.feeder_id, request.fault_type)
    except FaultApplicationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    key = _registry_key(request.feeder_id, request.fault_type)
    _active_faults[key] = (request.feeder_id, request.fault_type)
    _rebuild_live_state()
    return _current_grid_state()


@app.post("/grid/fault/clear", response_model=GridState)
@synchronized
def post_clear_fault(request: ClearFaultRequest) -> GridState:
    if request.feeder_id not in _active_faults:
        raise HTTPException(status_code=404, detail=f"no active fault registered under {request.feeder_id!r}")
    del _active_faults[request.feeder_id]
    _rebuild_live_state()
    return _current_grid_state()


@app.post("/grid/fault/clear_all", response_model=GridState)
@synchronized
def post_clear_all_faults() -> GridState:
    _active_faults.clear()
    _applied.reset()
    _seed_battery_energy_from_baseline()
    _rebuild_live_state()
    return _current_grid_state()


class ReplayControl(BaseModel):
    action: Literal["play", "pause", "step", "restart", "tick"]


@app.get("/grid/replay")
@synchronized
def get_replay() -> dict:
    return _replay.snapshot() if _replay else {"mode": "synthetic_baseline", "playing": False}


@app.post("/grid/replay/control")
@synchronized
def control_replay(request: ReplayControl) -> dict:
    if _replay is None:
        raise HTTPException(status_code=409, detail="Public replay is disabled (GRID_DATA_MODE=baseline)")
    previous_index = _replay.index
    if request.action == "play":
        _replay.playing = _replay.index < len(_replay.data["samples"]) - 1
    elif request.action == "pause":
        _replay.playing = False
    elif request.action == "restart":
        _replay.index = 0
        _replay.playing = False
        _active_faults.clear()
        _applied.reset()
        _seed_battery_energy_from_baseline()
    elif request.action == "step":
        _replay.playing = False
        _replay.step()
    elif request.action == "tick" and _replay.playing:
        _replay.step()
    if _replay.index != previous_index or request.action == "restart":
        _rebuild_live_state()
    return get_replay()


if _replay is not None:
    _rebuild_live_state()
