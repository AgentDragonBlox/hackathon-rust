# Cross-Service Integration Guidelines — read this file first

**For:** all three teammates and their AI coding assistants.

Each service now has its own AI-authored `INTEGRATION.md`
(`grid_engine/INTEGRATION.md`, `agent_engines/INTEGRATION.md`, and
eventually the orchestrator's own doc). Three independently-written docs
drift and duplicate each other fast. This file is the fix: **read this
one first**, then go to the specific service doc you actually need.

## Rule zero — how the docs stay from clashing

- Each service's `INTEGRATION.md` documents **only that service's own
  routes**. If you need to describe another service's endpoint, link to
  its doc — don't restate its shape here or there. Restated shapes go
  stale the moment the real one changes.
- Any change to `shared/contracts.py` gets **one line in the log below**,
  in the same commit/PR that makes the change — not just announced in
  chat. This file is the single place to check "did the shared contract
  change under me since I last looked."
- If your AI assistant notices a call-order or shape assumption here that
  looks wrong or has changed, it should flag it in this file explicitly,
  not silently reconcile it on its own — three separately-AI-authored
  codebases silently self-correcting in different directions is exactly
  the failure mode this file exists to prevent.

## Service registry

| Service | Owner | Port | Doc | Status (as of last sync) |
|---|---|---|---|---|
| grid_engine | Person 1 | 8001 | `grid_engine/INTEGRATION.md` | 98/98 tests passing, real pandapower AC power flow + Milestone 6 fault injection (real topology-based islanding, not simulated) |
| agent_engines | Person 2 | 8002 | `agent_engines/INTEGRATION.md` | Confirmed working end-to-end, tested against a real running server |
| orchestrator | Person 3 | — | *(not written yet)* | Runs the full loop against both services above |

## Canonical call sequence (current, whole-system)

1. Person 3 → `grid_engine` `GET /grid/state` and/or reacts to a predicted overload.
2. Person 3 → `agent_engines` `POST /agents/offers`
3. Person 3 → `agent_engines` `POST /agents/clear_market`
4. Person 3 → `grid_engine` `POST /grid/validate` with the actions from step 3
5. Person 3 keeps only `feasible: true` results — **substitute `adjusted_kw_amount`
   for `kw_amount` wherever it's set**, this is a partial approval, not a rejection
6. Person 3 → `agent_engines` `POST /agents/settle` with the adjusted, feasible actions only
7. Person 3 builds `BlockchainTransaction` from the `Trade` list `/agents/settle` returns

**Separately, for demo scenarios (not part of the above sequence):**
Person 3 → `grid_engine` `POST /grid/fault` to inject a simulated event
(`line_fault`, `grid_outage`, `battery_failure`, `solar_drop`,
`demand_spike`, `feeder_overload`) and `POST /grid/fault/clear`(`_all`)
to remove it. Unlike everything above, this actually mutates
grid_engine's live state: every `GET /grid/state` keeps reflecting an
injected fault (including real `islands` data — see next point) until
it's cleared. See `grid_engine/INTEGRATION.md`'s "Fault type mapping"
section for exact effects and ASSUMED magnitudes.

## ⚠️ Known cross-service inconsistency — flagging, not silently fixing

`POST /grid/validate` expects a **wrapped object**: `{"actions": [...]}`
(a real pydantic model, `ValidateActionsRequest`).
`POST /agents/settle` expects a **bare array**: `[...]` — no wrapper,
because no `SettleRequest` model exists in `contracts.py`.

Both are correct and tested *for their own service*. The risk is an AI
assistant pattern-matching from one to write a client for the other and
sending the wrong shape to one of them. **Not retroactively unifying
this** — both are already tested and working; changing either risks
breaking something that's currently correct. Person 3's orchestrator
client code should NOT share one generic "POST a list" helper between
these two calls without explicitly handling this difference.

## Shared contract changes log

- **Person 1** added `ValidateActionsRequest` to `shared/contracts.py`
  for `/grid/validate`. Pull latest before building against it.
- **Person 1** added `ClearFaultRequest` to `shared/contracts.py` for
  `/grid/fault/clear` (Milestone 6). Pull latest before building against
  it. Also flagging: `FaultInjectionRequest` (already in `contracts.py`,
  filed under Person 2's composite-shapes section) turned out to be
  grid_engine's endpoint, not agent_engines' — see the "Fault type
  mapping" known-gaps row in `grid_engine/INTEGRATION.md` for the detail.
  Not moving the section header/comment unilaterally.
- *(add one line per future change here — don't let this go stale)*

## Changelog convention

Each service keeps its own `CHANGELOG.md` (e.g. `agent_engines/CHANGELOG.md`)
— newest entry first, and **every entry states explicitly whether another
person needs to act**, not just what changed. This replaces spinning up a
new "addressed to Person X" file per change, which would just recreate
the drift problem this guidelines file exists to prevent.

**The rule:** if an entry's "Action needed" names another person, add a
**one-line pointer** to "Open items across the whole system" below,
linking to that changelog entry. This file stays the single place to
check "does anything need my attention right now" — the changelog is
where the detail lives once you know to look.

## ⚠️ Verified gap: `/agents/settle` and `/agents/reserve` are documented but not implemented

*(Original finding by Person 1, preserved below. Update from Person 2
follows — do not delete either; this is the audit trail.)*

Found by actually running both services together and calling the real
canonical sequence end-to-end (not a guess — `grid_engine`'s AI assistant
merged `grid-infra` and `agent-engine` locally in a scratch copy, started
both servers, and called `offers` → `clear_market` → `grid/validate` for
real; all three of those worked correctly against real HTTP responses).
Step 6, `POST /agents/settle`, returned a plain FastAPI `404 Not Found`.

Checked `agent_engines/main.py` directly on `origin/agent-engine`: it
only registers `/agents/offers`, `/agents/clear_market`, `/health`, and
`/agents/debug/ev_deadlines`. There is no `/agents/settle` or
`/agents/reserve` route. `agent_engines/ledger.py` and
`agent_engines/run_market.py` are both present but empty (0 bytes) on
that branch — matches: whatever built `Trade`/settlement logic
`agent_engines/INTEGRATION.md` describes and shows a "verified real
response" for doesn't appear to be committed yet. Also,
`agent_engines/requirements.txt` (referenced in that doc's own "Run it"
section) doesn't exist anywhere in the repo — only the root
`requirements.txt` (grid_engine's) does.

**Not a grid_engine bug, not silently worked around** — steps 1–5 of the
canonical sequence above are for real, verified compatible; only step 6
onward is currently blocked. Person 2, worth a look before Person 3
builds a `AgentClient.settle()` against a route that isn't live yet.

**Update from Person 2 (2026-09-12):** confirmed the diagnosis — the code
had been tested locally but never fully pushed. Fixed: re-verified the
whole module from a clean copy, confirmed all 6 routes register
correctly and no files are empty, re-pushed as one complete commit.
Full detail in `agent_engines/CHANGELOG.md`'s top entry.
**Status: fix pushed, awaiting Person 1 re-verification** — please re-run
your merge-and-test method against the latest push rather than taking
this update's word for it; that's exactly the standard this whole file
is trying to hold everyone to.

## Open items across the whole system

- **Primary Rust demo (2026-09-13):** defaults are live Rust agents and the
  public-profile Python grid. Failed settlement creates no fallback trades.
  Shared contracts are unchanged. Use the updated orchestrator for playback;
  see `grid_engine/INTEGRATION.md` and `README.md`. Rust clearing preserves
  request order to match cumulative grid validation.

- **Person 1:** re-verify the settle/reserve fix above using your
  merge-and-test method — see `agent_engines/CHANGELOG.md`'s top entry.
- **Person 3:** `/agents/settle` and `/agents/reserve` are now live —
  see `agent_engines/CHANGELOG.md`'s second entry for exactly what to
  call and how `adjusted_kw_amount` factors in.
- **Person 3:** confirm the orchestrator substitutes `adjusted_kw_amount`
  for `kw_amount` before calling `/agents/settle` on a partial approval —
  flagged earlier, not yet confirmed tested against that specific case.
- **Resolved:** whether grid_engine needs an endpoint to commit a settled
  trade into its own live state — Person 3 confirmed (as demo owner) the
  orchestrator's own trade/blockchain history covers this for the demo.
  No `POST /grid/execute` planned. See `grid_engine/INTEGRATION.md`'s
  "State model" section for the full reasoning.
- **Person 3, worth confirming:** `/grid/validate` checks actions
  **cumulatively** — action 2 in a batch is validated against the state
  action 1 would leave behind, not independently. This means the *order*
  of actions in the list you send affects the result. Worth confirming
  your orchestrator doesn't assume validation results are order-independent.
- **Low priority, team housekeeping:** `FaultInjectionRequest` is filed
  under a comment in `contracts.py` that says "wire shapes for
  /agents/offers," but it's actually grid_engine's own endpoint
  (`/grid/fault`). Nobody's fixed the comment yet since it means editing
  the shared file — fine to leave until a natural round of contract
  cleanup, not worth an ad-hoc fix.
- **Whoever owns the final demo:** decide if grid_engine needs to know
  about settled trades at all for demo purposes, or if the dashboard only
  ever needs the orchestrator's own view of what happened.
- **Resolved for Person 3:** an earlier check-in mentioned an "islanding
  visual" already built into the dashboard. As of Milestone 6,
  `GridState.islands` is real (topology-based connected-components
  analysis after a `line_fault` or `grid_outage`, not a placeholder) —
  worth confirming whether that visual is already wired to poll
  `GET /grid/state` and render this field, or still using mock data.

## Known scope not covered by any milestone yet

- `FaultInjectionRequest`'s `feeder_id` is required by the schema but
  `grid_outage` doesn't actually use it (it's a whole-campus fault, not
  feeder-scoped) — any of F1/F2/F3 is accepted with identical effect.
  Flagged in `grid_engine/INTEGRATION.md`'s known-gaps table; not fixed
  since it'd mean changing the shared request shape.
- No fault type touches the transformer directly (only line/ext_grid/
  battery/sgen/load elements) — if a demo scenario needs a transformer
  fault specifically, that's new scope, not something `POST /grid/fault`
  currently supports.

## Note to any AI assistant reading this

- Read the specific service's own `INTEGRATION.md` in full before writing
  a client for it — this file only summarizes, it's not the source of truth
  for any single service's exact shapes.
- Never copy another service's route documentation into your own service's
  doc. Link to it instead.
- If you change `shared/contracts.py`, add a line to the log above in the
  same commit — don't leave it as something only mentioned in chat.
