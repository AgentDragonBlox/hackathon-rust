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
Milestone 5's /grid/validate is still deliberately non-mutating -- it
validates against `_net` via scenarios.validate_actions(), which works
on its own deep copy and never touches `_net` (see scenarios.py's own
docstring). There is still no endpoint that commits an executed/settled
action into `_net` -- that flagged gap from Milestone 5 is unchanged; see
grid_engine/INTEGRATION.md.

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

import pandapower as pp
from fastapi import FastAPI, HTTPException

from grid_engine.contracts_adapter import build_grid_state
from grid_engine.faults import FaultApplicationError, apply_fault, compute_islands
from grid_engine.grid import build_campus_network, run_power_flow
from grid_engine.scenarios import validate_actions
from shared.contracts import (
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
def get_grid_state() -> GridState:
    return _current_grid_state()


@app.post("/grid/validate", response_model=list[ValidationResult])
def post_validate(request: ValidateActionsRequest) -> list[ValidationResult]:
    return validate_actions(_net, request.actions)


@app.post("/grid/fault", response_model=GridState)
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
def post_clear_fault(request: ClearFaultRequest) -> GridState:
    if request.feeder_id not in _active_faults:
        raise HTTPException(status_code=404, detail=f"no active fault registered under {request.feeder_id!r}")
    del _active_faults[request.feeder_id]
    _rebuild_live_state()
    return _current_grid_state()


@app.post("/grid/fault/clear_all", response_model=GridState)
def post_clear_all_faults() -> GridState:
    _active_faults.clear()
    _rebuild_live_state()
    return _current_grid_state()
