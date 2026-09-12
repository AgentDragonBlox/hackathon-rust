# Grid Engine (Person 1) — Integration Guide

**For:** Person 2 (Agents/Market) and Person 3 (Orchestrator).

This document was written after building AND actually running this
service — every request/response example below was captured from a real
HTTP call (via FastAPI's `TestClient`) against a real pandapower network
that had AC power flow actually solved on it, not written from memory or
assumed shapes. Treat the shapes here as ground truth for what this
service really does today. Where something is a known gap or open
question, it's labeled as such — don't silently paper over those,
surface them so the team can decide together.

This document directly answers the open question at the end of Person
2's own `INTEGRATION.md` ("does your grid engine expose a
`POST /grid/validate`?") — yes, see below.

---

## What this is

A FastAPI service (`grid_engine/api.py`) wrapping a real 5-bus radial
LV/MV microgrid model built in pandapower (`grid_engine/grid.py`):
utility (11kV) → transformer (1MVA, tapped) → campus_main (0.415kV) →
three feeders (F1/F2/F3) → hospital / academic / EV-charging / facility
loads, one solar sgen, one battery (storage). All request/response models
not already in `shared/contracts.py` are the single `ValidateActionsRequest`
appended to that file (see "Known gaps" below) — everything else is
`contracts.py` types, same rule Person 2 followed for their service.

**Run it:**
```bash
pip install -r requirements.txt
uvicorn grid_engine.api:app --port 8001 --reload
```
**Verify it's up:** `curl http://localhost:8001/health` → `{"status":"ok"}`

**Verify the core logic works standalone** (no server needed):
```bash
python3 -m pytest grid_engine/tests/ -v
```

---

## Routes

### 1. `GET /health`
**Response:** `{"status": "ok"}`

### 2. `GET /grid/state`
**Response:** `GridState` (from `contracts.py`) — describes the grid
engine's one in-memory baseline network (see "State model" below for
exactly what "baseline" means here).

**Verified real response** (this exact call was made and returned this):
```json
{
  "timestamp": "2026-09-12T10:07:43.494346Z",
  "assets": [
    {"asset_id": "hosp-1",  "asset_type": "hospital", "current_load_kw": 380.0, "current_gen_kw": 0.0, "soc_percent": null, "min_reserve_percent": null, "online": true},
    {"asset_id": "acad-1",  "asset_type": "academic", "current_load_kw": 240.0, "current_gen_kw": 0.0, "soc_percent": null, "min_reserve_percent": null, "online": true},
    {"asset_id": "ev-1",    "asset_type": "ev",       "current_load_kw": 70.0,  "current_gen_kw": 0.0, "soc_percent": null, "min_reserve_percent": null, "online": true},
    {"asset_id": "fac-1",   "asset_type": "factory",  "current_load_kw": 150.0, "current_gen_kw": 0.0, "soc_percent": null, "min_reserve_percent": null, "online": true},
    {"asset_id": "solar-1", "asset_type": "solar",    "current_load_kw": 0.0,   "current_gen_kw": 165.0, "soc_percent": null, "min_reserve_percent": null, "online": true},
    {"asset_id": "batt-1",  "asset_type": "battery",  "current_load_kw": 0.0,   "current_gen_kw": 0.0, "soc_percent": 72.0, "min_reserve_percent": 10.0, "online": true}
  ],
  "feeders": [
    {"feeder_id": "F1", "loading_percent": 71.03474770620912, "connected_assets": ["hosp-1", "batt-1"], "status": "normal"},
    {"feeder_id": "F2", "loading_percent": 93.29571867153497, "connected_assets": ["acad-1", "ev-1", "solar-1"], "status": "warning"},
    {"feeder_id": "F3", "loading_percent": 85.04742042891407, "connected_assets": ["fac-1"], "status": "normal"},
    {"feeder_id": "TRANSFORMER", "loading_percent": 77.79019353651296, "connected_assets": ["hosp-1", "batt-1", "acad-1", "ev-1", "solar-1", "fac-1"], "status": "normal"}
  ],
  "predictions": [],
  "active_faults": [],
  "islands": []
}
```
Note `"TRANSFORMER"` is a synthetic `feeder_id` — see "Known gaps" below,
`NetworkState` has no real transformer field yet. Note also
`predictions` is always `[]` today — see the same section.

### 3. `POST /grid/validate`
**Request:** `ValidateActionsRequest` — `{"actions": [ProposedAction, ...]}`.
**Response:** `list[ValidationResult]`, one per action, **in the same
order as the request**.

Validates each action **cumulatively**: action 2 is checked against the
state that would exist if action 1 had already been accepted (not
independently against the same starting state) — this matters if your
orchestrator is deciding whether a *batch* of actions from one
`clear_market` call is jointly feasible, not just individually feasible.
An action that's electrically infeasible is not applied before checking
the next one; the next one is still validated against the state as it
stood before the rejected one. Nothing is ever mutated server-side (see
"State model" below) — call it as many times as you like.

**Verified real request** (Person 2's own verified `clear_market`
response from their `INTEGRATION.md`, posted here as-is):
```json
{
  "actions": [
    {"action_id": "act-1", "request_id": "req-1", "asset_id": "ev-1",   "action_type": "ev_delay",       "kw_amount": 14.0, "source_offer_id": "offer-ev"},
    {"action_id": "act-2", "request_id": "req-1", "asset_id": "acad-1", "action_type": "hvac_reduction",  "kw_amount": 28.0, "source_offer_id": "offer-acad"}
  ]
}
```
**Verified real response:**
```json
[
  {"action_id": "act-1", "feasible": true, "reason": null, "adjusted_kw_amount": null},
  {"action_id": "act-2", "feasible": true, "reason": null, "adjusted_kw_amount": null}
]
```

**Verified real response for an oversized request** (battery asked for
500kW, rated at 250kW — capped, not rejected):
```json
[
  {"action_id": "act-3", "feasible": true, "reason": "capped at asset's available capacity: 250.0 kW of 500.0 kW requested", "adjusted_kw_amount": 250.0}
]
```

**Verified real response for a genuinely infeasible action** (fully
shedding academic's 240kW load pushes its bus voltage to 1.0593pu,
outside the [0.95, 1.05]pu healthy band — this is a real power-flow
result, not a made-up example):
```json
[
  {"action_id": "act-4", "feasible": false, "reason": "bus academic voltage 1.0593 pu outside [0.95, 1.05] pu", "adjusted_kw_amount": null}
]
```

**What "feasible" is checked against:** every feeder (F1/F2/F3) and the
transformer must have `loading_percent <= 100%`, and every bus voltage
must be in `[0.95, 1.05]` pu, after actually rerunning AC power flow with
the action applied (capped at the asset's own physical limit first, if
it exceeds that). A power flow that fails to converge is reported as
infeasible with a clear reason too, never silently treated as "no
violations found." See `grid_engine/scenarios.py`'s module docstring for
full detail on how each `action_type` maps to a network change.

### 4. `POST /grid/fault`
**Request:** `FaultInjectionRequest` (from `contracts.py`) — `{"feeder_id", "fault_type"}`.
**Response:** `GridState`, reflecting the fault **immediately and from
then on** — see "State model" below, this is the one part of this
service that actually mutates. `fault_type` is one of `solar_drop`,
`demand_spike`, `feeder_overload`, `battery_failure`, `grid_outage`,
`line_fault` (contracts.py's own enum). See "Fault type mapping" below
for what each one actually does and which `feeder_id` it requires.

**Verified real request/response** (`line_fault` on F1 — hospital's
feeder — islands hosp-1/batt-1, verified via real topology analysis, not
assumed):
```json
// POST /grid/fault
{"feeder_id": "F1", "fault_type": "line_fault"}
```
```json
// 200 response (assets/feeders trimmed to the interesting bits)
{
  "assets": [
    {"asset_id": "hosp-1", "current_load_kw": 0.0, "online": true, "...": "..."}
  ],
  "feeders": [
    {"feeder_id": "F1", "loading_percent": 0.0, "status": "faulted", "...": "..."},
    {"feeder_id": "F2", "loading_percent": 91.93225864906931, "status": "warning", "...": "..."}
  ],
  "active_faults": ["F1"],
  "islands": [["batt-1", "hosp-1"]]
}
```
Note `hosp-1.current_load_kw` is `0.0`, not missing/null — real
pandapower behavior for an in-service load on an unreachable bus,
confirmed by direct experiment. `online` stays `true`: the asset itself
didn't fail, it's just cut off (that's exactly what `islands` conveys).

An invalid `feeder_id`/`fault_type` combination (e.g. `solar_drop` on
F1, which has no solar) returns `400` with a clear reason, and is never
registered as active. An unrecognized `fault_type` value is rejected by
FastAPI/pydantic itself (`422`) before grid_engine's own logic runs, since
it's a `Literal` in `contracts.py`.

### 5. `POST /grid/fault/clear`
**Request:** `ClearFaultRequest` (added to `contracts.py` this milestone
— see "Shared contract changes" in `INTEGRATION_GUIDELINES.md`) —
`{"feeder_id"}`. This is the *registry key*, not necessarily the
`feeder_id` you originally submitted — for every `fault_type` except
`grid_outage` those are the same string; for `grid_outage` the key is the
synthetic `"GRID"` (see "Fault type mapping" below). Clearing a
never-injected key returns `404`.
**Response:** `GridState`, rebuilt from the pristine baseline plus every
*other* still-active fault (never a partial "undo" — see "State model").

### 6. `POST /grid/fault/clear_all`
No request body. Clears every active fault at once and returns the
resulting (pristine-baseline) `GridState`. Handy for a demo reset button.

---

## Fault type mapping (what each `fault_type` actually does here)

`FaultInjectionRequest` has no magnitude field (how much solar drops, how
big a demand spike) and no `asset_id` (which specific line/battery within
a feeder) — these ASSUMED constants (`grid_engine/faults.py`) fill that
gap, each verified against a real power-flow rerun, not just asserted:

| `fault_type` | Valid `feeder_id` | Effect |
|---|---|---|
| `line_fault` | F1, F2, or F3 | Takes that feeder's own line out of service. Real topology analysis (`faults.compute_islands`, via pandapower's own graph + networkx connected-components) determines which asset_ids become unreachable from the utility — reported in `GridState.islands`. |
| `grid_outage` | F1, F2, or F3 (value **not actually used** — flagged below) | Disables the `ext_grid`. No reference bus anywhere in the network means AC power flow has **no solution at all** (verified: pandapower raises `UserWarning`, not `LoadflowNotConverged`) — the whole campus becomes one island, every feeder reports `loading_percent: 0.0` / `status: "islanded"`, every asset `0` kW. Registry key is the synthetic `"GRID"`. |
| `battery_failure` | F1 only (batt-1's feeder) | Takes the hospital battery's `storage` element out of service. Verified: an offline battery reports `0` kW, not `NaN`. |
| `solar_drop` | F2 only (solar-1's feeder) | Reduces solar-1's output to `ASSUMED_SOLAR_DROP_REMAINING_FRACTION` (15%) of its current value — an assumed ~85% drop (heavy cloud cover / partial panel fault). |
| `demand_spike` | F1, F2, or F3 | Scales every load asset on that feeder by `1 + ASSUMED_DEMAND_SPIKE_FRACTION` (+40%), power factor preserved. |
| `feeder_overload` | F1, F2, or F3 | Searches (real reruns, not a linear guess — a forward scan then a binary-search refinement, since AC power flow can stop converging well before an arbitrarily large load scale) for the smallest load-scale factor that pushes that feeder's **real, rerun** line loading past 105%, then applies it. Verified for all three feeders: each lands at ~105.0%, confirmed by an independent rerun in the test suite. |

---

## Action type mapping (what each `action_type` actually does here)

| `action_type` | Effect |
|---|---|
| `load_reduction`, `hvac_reduction`, `production_flex` | Reduces the mapped load's real power by `kw_amount` (reactive power scaled to keep the same power factor). Capped at the asset's current draw. |
| `ev_delay` | Same effect as the load-reduction types above — deferring a charge session reduces *right-now* demand the same way shedding would; grid_engine doesn't need to distinguish "deferred" from "cancelled." |
| `battery_discharge` | Increases the mapped battery's discharge. Capped at the smaller of its rated power (`max_p_mw`) and an energy-derived limit (available energy above its reserve floor, divided by an ASSUMED 15-minute sustain window — flagged in code, not in `contracts.py`). |
| `emergency_reserve` | **No-op.** Per Person 2's own `INTEGRATION.md`, this represents a standing capacity commitment (`ReserveContract`), not an immediate dispatch — validating it just confirms it doesn't break anything against the unmodified state. |

---

## State model — read this before wiring the orchestrator

`grid_engine/api.py` holds a **pristine baseline** network (built once at
process start from `grid.build_campus_network()` and solved once,
`_baseline_net` — never mutated after that) and a **live** network
(`_net`) that `GET /grid/state` actually describes. Until a fault is
injected, the live network just mirrors the baseline, so `GET /grid/state`
looks like a fixed snapshot — it still doesn't advance with wall-clock
time or a running simulation on its own.

`POST /grid/validate` is still deliberately **non-mutating**: it
validates against a deep copy internally and never changes what a later
`GET /grid/state` sees. Concretely: **there is still no endpoint that
commits an executed action into grid_engine's live state.** If the
orchestrator's real flow is `offers → clear_market → validate → settle →
(grid actually changes)`, that last step has nothing to call yet.

**Resolved as a team decision, not left open:** Person 3 confirmed (as
demo owner) that grid_engine does **not** need to build this — the
orchestrator already holds the canonical trade/blockchain history for
dashboard purposes, so `GET /grid/state` never reflecting a settled
trade is fine for the demo. No `POST /grid/execute` (or similar) is
planned. This paragraph is kept accurate to what the *code* does today
(still no such endpoint), not as an open question anymore — see
`INTEGRATION_GUIDELINES.md`'s "Open items" log for the decision record.

**Fault injection IS mutating**, and is the one exception to the above.
`POST /grid/fault` registers a fault; every registered fault stays
"active" until explicitly cleared, and `_net` is always **rebuilt from
`_baseline_net` from scratch** (replaying every still-active fault),
never patched incrementally — so clearing one fault can never leave a
stray mutation behind from a different one. This is what lets a
dashboard poll `GET /grid/state` and show an ongoing degraded/islanded
condition rather than a one-off computed result. A fault registered on a
feeder that already has one active **replaces** it rather than stacking
(matches `GridState.active_faults` being feeder-id-keyed per
`contracts.py`'s own comment: "feeder_ids currently faulted") — two
faults on two *different* feeders coexist fine.

---

## Asset ID mapping (a real integration decision — flagged, not silent)

`shared/contracts.py`'s `AssetState.asset_id` / `ProposedAction.asset_id`
are just free-form `str` — there's no team-agreed ID scheme. This service
adopted Person 2's own verified example IDs (`hosp-1`, `acad-1`, `ev-1`,
`batt-1`) exactly, plus `fac-1` and `solar-1` for the two assets their
example didn't cover, so both services speak the same asset IDs out of
the box:

| `asset_id` | pandapower element | Notes |
|---|---|---|
| `hosp-1` | load `hospital_load` | |
| `acad-1` | load `academic_load` | |
| `ev-1` | load `ev_load` | |
| `fac-1` | load `facility_load` | mapped to `asset_type="factory"` — see Known gaps |
| `solar-1` | sgen `solar` | |
| `batt-1` | storage `hospital_bess` | |

This mapping lives in exactly one place: `grid_engine/contracts_adapter.py`'s
`ASSET_ID_TO_INTERNAL`. If the team wants a different scheme, that's the
only file to change.

---

## Known gaps — decide with the team, don't just assume

| Gap | Where it's handled now | If you want it fixed properly |
|---|---|---|
| `AssetState.asset_type` has no generic office/admin facility type — the `Literal` enum is `("hospital","academic","factory","battery","solar","ev","utility")` | `facility_load` mapped to `"factory"` as the closest fit (semantically off — factory implies industrial/production load, which also overlaps Person 2's `production_flex` offer type) | Add a real `"facility"` or `"office"` value to the shared enum |
| `NetworkState` has no transformer field, but grid_engine tracks transformer loading as a real, separate constraint | Represented as a synthetic `feeder_id="TRANSFORMER"` entry in `GridState.feeders` | Add a proper `transformer` field (or list) to `GridState` |
| `GridState.predictions` is always `[]` from `/grid/state` | There's no server-side rolling-history loop wired into `api.py` yet — `forecasting.py`'s linear-trend forecaster is real and tested, but needs a `history` buffer that persists across calls, which doesn't exist here yet (same shape of gap as Person 2's own "statefulness gap" note) | Wire a live-history loop into `api.py` in a future milestone; `contracts_adapter.build_grid_state()` already accepts an optional `predictions` argument for whenever that exists |
| No endpoint to commit an executed action into live state after `/agents/settle` | N/A — doesn't exist | **RESOLVED, not needed:** Person 3 confirmed the orchestrator's own trade/blockchain history covers this for the demo (see "State model" above) |
| `FaultInjectionRequest` is filed under Person 2's section of `contracts.py` (comment says "wire shapes for /agents/offers"), but every `fault_type` is a physical grid event | Treated as grid_engine's own — nothing in `agent_engines/INTEGRATION.md` claims a route for it | Move the comment/section header in a team-agreed pass over `contracts.py`; not changed unilaterally, per the "don't fork copies" rule |
| `FaultInjectionRequest` has no magnitude field and no `asset_id` (which line/battery within a feeder with more than one) | ASSUMED constants in `grid_engine/faults.py` (`ASSUMED_SOLAR_DROP_REMAINING_FRACTION`, `ASSUMED_DEMAND_SPIKE_FRACTION`, `FEEDER_OVERLOAD_TARGET_PCT`), each documented at its definition | Add real fields if the team wants demo control over fault severity |
| `grid_outage`'s `feeder_id` is required by the schema but not used — it's a whole-campus fault, not feeder-scoped | Any of F1/F2/F3 accepted, identical effect; registry key is the synthetic `"GRID"` | Give `FaultInjectionRequest` an optional `feeder_id` (or a separate, feeder-id-less request shape for whole-grid faults) |
| `NetworkState.status`'s `"islanded"` value is used for two different real situations | (1) genuine topological disconnection (a `line_fault`'s downstream bus), and (2) a real power-flow non-convergence that leaves the network topologically connected but numerically unsolved (`islands` will correctly report `[]` in that second case — check `active_faults` + which feeders show `"islanded"` together to tell them apart) | Add a distinct status value (e.g. `"unsolved"`) to the shared enum if this distinction matters for the demo |

**Note for Person 2 specifically:** none of the gaps above block anything
on your side — your service already works fine against the generic
`AssetState`/`GridState` fields it has today, per your own
`INTEGRATION.md`'s note back to us. Only worry about these if the team
decides to extend `contracts.py`; ping this service's owner first (per
the "don't fork copies" rule at the top of `contracts.py`).

**Note for Person 3 specifically:** call `/grid/validate` between
`clear_market` and `settle`, passing exactly the `ProposedAction` list
you got back from `/agents/clear_market` (same shape, no translation
needed — both services import the same `ProposedAction` from
`contracts.py`). A minimal reference client, mirroring the `AgentClient`
shape from Person 2's own doc:

```python
import httpx

class GridClient:
    def __init__(self, base_url="http://localhost:8001"):
        self.base_url = base_url

    def get_state(self) -> dict:
        r = httpx.get(f"{self.base_url}/grid/state")
        r.raise_for_status()
        return r.json()

    def validate(self, actions: list[dict]) -> list[dict]:
        r = httpx.post(f"{self.base_url}/grid/validate", json={"actions": actions})
        r.raise_for_status()
        return r.json()

    def inject_fault(self, feeder_id: str, fault_type: str) -> dict:
        r = httpx.post(f"{self.base_url}/grid/fault", json={"feeder_id": feeder_id, "fault_type": fault_type})
        r.raise_for_status()
        return r.json()

    def clear_fault(self, feeder_id: str) -> dict:
        r = httpx.post(f"{self.base_url}/grid/fault/clear", json={"feeder_id": feeder_id})
        r.raise_for_status()
        return r.json()

    def clear_all_faults(self) -> dict:
        r = httpx.post(f"{self.base_url}/grid/fault/clear_all")
        r.raise_for_status()
        return r.json()
```
This mirrors the exact calls verified working in
`grid_engine/tests/test_api.py`, `test_faults.py`, and
`test_api_faults.py` — if in doubt about a shape, those files (and this
doc) are the ground truth.
