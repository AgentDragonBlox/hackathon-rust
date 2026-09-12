# Changelog — agent_engines (Person 2)

Every change to this service gets an entry here, **newest first**. Each
entry states explicitly whether Person 1 or Person 3 need to do anything
— check that field before skimming past an entry as irrelevant to you.

**If an entry's "Action needed" is anything other than "None"**, it also
gets a one-line pointer added to `INTEGRATION_GUIDELINES.md`'s "Open
items across the whole system" section — so nobody has to proactively
read this whole file to notice something's expected of them. This file
is the detail; the guidelines file is what surfaces that detail exists.

---

## 2026-09-12 — Added: automated pytest suite + CI workflow

**What changed:** Real, repeatable tests replacing the manual
uvicorn+httpx verification used during development — `test_agents.py`
(11 tests, pure agent logic), `test_clearing.py` (6 tests, merit-order/
fairness/settlement), `test_api.py` (11 tests, full route surface via
FastAPI's `TestClient` — no real server needed). 28 tests, all passing,
under a second to run. Also added `.github/workflows/agent_engines_tests.yml`
— runs on every push/PR touching `agent_engines/` or `shared/`.

**Why:** direct response to the settle/reserve-never-pushed incident.
CI running on exactly what's in a branch (not what's on someone's
laptop) would have caught that the moment it happened, automatically —
no need for Person 1 to manually merge branches and test by hand to
discover it.

**Action needed — Person 1 / Person 3:** none — purely additive testing
infrastructure on this service, no behavior or contract change.

---

## 2026-09-12 — Fix: no flexibility source existed for F3 (factory-only feeder)

**What happened:** Person 1's grid_engine `INTEGRATION.md` revealed the
real campus topology — F3 connects to exactly one asset, `fac-1`
(`asset_type="factory"`, 150 kW). `agent_engines`' `AGENT_FUNCS` only
covered hospital/academic/ev/battery. Confirmed by direct test: an F3
overload request against `/agents/offers` returned `[]` — meaning F3
could never be resolved at all, not just resolved poorly.

**Fixed:** added `factory_agent.py`, offering `production_flex` (already
a valid `offer_type` in `shared/contracts.py`, and already handled by
grid_engine's action-type mapping identically to `load_reduction`/
`hvac_reduction` — no contract change needed, the pieces already existed
on both sides, just not connected). Wired into `AGENT_FUNCS`. Added a
permanent regression scenario (`f3_factory_only` in `mock_states.py`) so
this can't silently regress. Re-verified from a clean copy: F3's request
now clears correctly.

**Action needed — Person 1:** none — this only touches agent_engines'
own dispatch table, no contract or grid_engine change involved.

**Action needed — Person 3:** none — same routes, same shapes, just a
previously-missing asset type now covered. Nothing about how you call
`/agents/offers` changes.

---

## 2026-09-12 — Fix: settle/reserve/requirements.txt missing from origin/agent-engine

**What happened:** Person 1 merged `grid-infra` and `agent-engine`
locally, ran the real canonical sequence end-to-end against live HTTP,
and found `/agents/settle` returned a plain 404 on `origin/agent-engine`.
`settlement.py`, `reserve.py`, and `agent_engines/requirements.txt` had
been built and tested locally but never actually landed in a pushed
commit. `ledger.py` and `run_market.py` were present on that branch but
empty — content cleared locally, replacement never saved before commit.

**Fixed:** re-verified the entire module from a clean copy (all 6 routes
confirmed registered, no empty files, full `offers → clear_market →
settle` loop re-tested end-to-end), repackaged, and re-pushed as one
complete commit rather than trusting a previous partial push.

**Action needed — Person 1:** re-run your exact merge-and-call-the-real-
sequence test against the latest push to confirm this is actually
resolved, not just claimed resolved.

**Action needed — Person 3:** none yet — nothing was built client-side
against `/agents/settle` before this was caught.

---

## 2026-09-12 — Added: `/agents/settle` and `/agents/reserve`

**What changed:** `settlement.py` (builds a `Trade` from a confirmed
`ProposedAction`, looking up price via an in-memory offer cache) and
`reserve.py` (`ReserveContract` — not yet called by anything else in the
pipeline). Both wired into `main.py` as real routes.

**Why:** `shared/contracts.py`'s own docstring assigns `Trade` and
`ReserveContract` to Person 2. Needed for Person 3's orchestrator to
close the loop past grid validation.

**Action needed — Person 3:** call `/agents/settle` with the
grid-validated (and `adjusted_kw_amount`-corrected, where set) actions —
see the `AgentClient` reference in `agent_engines/INTEGRATION.md`.

**Action needed — Person 1:** none.

---

## 2026-09-12 — Migrated to shared/contracts.py; stood up as a real HTTP service

**What changed:** Dropped the earlier ad-hoc dict-based `schema.py` in
favor of `shared/contracts.py`'s typed pydantic models. Rebuilt as a
FastAPI service (`/agents/offers`, `/agents/clear_market`) per the
team's confirmed architecture decision — separate processes talking
HTTP, not in-process function calls.

**Why:** confirmed with Person 3 that `OffersRequest` / `ClearMarketRequest`
were meant as real wire shapes for a running service, not just
API-style-named objects passed in-process.

**Action needed — Person 1 / Person 3:** none — this was the initial
build, not a change to something already relied upon.
