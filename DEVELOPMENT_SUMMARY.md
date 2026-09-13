# Development Summary — Microgrid Resilience Exchange

**What this covers:** what the whole system does, how each piece fits
together, and exactly what changed when `agent_engines` moved from
Python to Rust — written so someone new to the repo (or a judge asking
"what did you actually build") can get the full picture in one read.

---

## What the system does

A predictive, targeted flexibility market for a small campus microgrid.
Instead of the standard blunt response to an overloaded feeder (cut power
to a wide area), this system:

1. **Predicts** stress on a specific feeder or transformer before it
   becomes a real overload (`grid_engine`).
2. **Targets** only the handful of assets actually connected to that
   feeder and asks them to offer flexibility (`agent_engines_rs`).
3. **Clears a market** among those offers — cheapest combination that
   resolves the shortfall, with a fairness penalty against assets
   tapped recently (`agent_engines_rs`).
4. **Validates** the proposed fix against real AC power-flow physics
   before anything executes (`grid_engine`).
5. **Settles** the trade and records it on an append-only, hash-chained
   ledger (`orchestrator`).
6. **Displays** all of it live — network topology, system status,
   blockchain ledger, agent activity feed — and lets a demo operator
   inject fault scenarios on demand (`orchestrator`'s dashboard, and the
   newer `dashboard/` SvelteKit rebuild).

## Service map

| Service | Language | Role | Port | Status |
|---|---|---|---|---|
| `grid_engine` | Python (pandapower) | Real AC power-flow model, validation, fault injection | 8001 | Original, unchanged |
| `agent_engines_rs` | **Rust** (Axum) | Flexibility market — agents, clearing, fairness, settlement | 8002 | **Rewritten from Python — see below** |
| `orchestrator` | Python (FastAPI + WebSocket) | Ties everything together: the tick loop, blockchain ledger, live dashboard feed | 8000 | Original, unchanged |
| `dashboard` | TypeScript (SvelteKit) | Richer frontend rebuild, deployable to Vercel | 5173 (dev) | New, connects to orchestrator's real WebSocket |
| `gateway` | TypeScript (Hono) | Thin proxy/BFF for the Vercel deployment | 8787 (dev) | New, no business logic of its own |

**The canonical request flow**, unchanged regardless of which language
answers on port 8002:
```
orchestrator → agent_engines_rs: POST /agents/offers
orchestrator → agent_engines_rs: POST /agents/clear_market
orchestrator → grid_engine:      POST /grid/validate
orchestrator → agent_engines_rs: POST /agents/settle
orchestrator → own ledger:       append BlockchainTransaction
orchestrator → all connected dashboards: broadcast over WebSocket
```

## Python → Rust: what changed, what didn't

`agent_engines` was fully rewritten in Rust and now **replaces** the
Python version — this repo no longer runs the Python `agent_engines` at
all (that folder was removed once the Rust version was verified working).
This is intentional: this fork is the dedicated Rust experiment. The
original Python `agent_engines` (and the rest of the Python stack) is
preserved unmodified at the root of the original, un-forked repo
(`andrew-0228/3rd_sem_hacka`) as the safety net if the Rust path doesn't
pan out in time.

### What's identical

- **The wire contract.** Same six routes, same request/response JSON
  shapes. `grid_engine` and `orchestrator` talk to `agent_engines_rs`
  exactly as they talked to the Python version — neither needed a single
  line changed.
- **The business logic.** Same per-agent constants (hospital 5%/$9.50,
  academic 30%/$4.20, EV 70%/$2.80 + deadline logic, battery reserve-floor
  logic, factory 20%/$6.50), same merit-order clearing, same fairness
  formula (`cost × (1 + 0.3 × recency_count)`).
- **Verified behavior.** Directly tested side-by-side against the same
  scenarios: identical offer amounts, identical clearing decisions,
  identical fairness drift across repeated calls, identical rejection
  messages.

### What's different

| Aspect | Python version | Rust version |
|---|---|---|
| Web framework | FastAPI | Axum |
| Shared mutable state (offer cache, fairness tracker, EV deadlines) | Module-level globals | `Arc<Mutex<AppState>>` — explicit, type-checked at compile time |
| Contract definitions | Imports `shared/contracts.py` directly | Independent mirror in `contracts.rs` — must be manually kept in sync if the Python contract changes |
| Datetime handling | pydantic accepts naive or timezone-aware timestamps | Needed a custom lenient deserializer to match that same leniency — Rust's default is stricter |
| CORS | Not needed until a browser client existed | Required explicit `tower-http` `CorsLayer` once the Svelte dashboard called it directly from a browser |
| Test suite | 28 pytest tests (`agent_engines/tests/`, now removed with the folder) | 17 `#[cfg(test)]` unit tests (agent logic, clearing, fairness, settlement) — **no API-level integration tests yet**, unlike the Python version's `test_api.py` |
| CI | `agent_engines_tests.yml` (still exists, now dormant since the Python folder is gone) | `agent_engines_rust_tests.yml` — build, test, and a live smoke test with a real CORS-header check |

### Honest gaps in the Rust version

- No equivalent of `test_api.py`'s 11 route-level tests (would need
  Axum's `tower::ServiceExt::oneshot` — dev-dependencies for this are
  already in `Cargo.toml`, just not used yet).
- `contracts.rs` is a hand-maintained mirror, not a generated one. If
  `shared/contracts.py` changes, `contracts.rs` needs a matching manual
  edit — nothing catches drift between them automatically.
- The old Python `agent_engines_tests.yml` workflow still exists and
  will simply do nothing useful now (no `agent_engines/` path left to
  trigger on) — harmless to leave, worth deleting in a future cleanup
  pass.

## Deployment topology

Vercel cannot host `agent_engines_rs`, `grid_engine`, or `orchestrator`
as-is — they're long-running processes with in-memory state, and
Vercel's serverless/edge functions don't guarantee that persists across
requests. Split:

- **Vercel:** `dashboard/` (SvelteKit, has the Vercel adapter) and
  `gateway/` (Hono, stateless proxy only).
- **A real host** (Fly.io / Railway / Render, or any VM): `grid_engine`,
  `agent_engines_rs`, `orchestrator` — all three need to keep running
  continuously.

## Real incidents worth knowing about (so they don't repeat)

- **A branch merge silently kept only 3 of 19 real files twice**, on two
  separate repos, both times through what looks like a browser-based
  merge conflict resolution rather than a local `git merge`. Worth
  merging locally and reading the diff before trusting any PR merge.
- **`uvicorn` without `[standard]` has no WebSocket support at all** —
  caused a long "reconnecting…" debugging session that turned out to be
  one missing package extra, not a code bug. Now fixed in `requirements.txt`.
- **A fork's `shared/contracts.py` silently lagged the original repo** —
  forks don't auto-sync, so `ValidateActionsRequest` and
  `ClearFaultRequest` (added later upstream) were missing here until
  patched directly.
- **`orchestrator`'s own code imported from `schemas.contracts`**, a
  module that never existed in this repo — a naming-convention mismatch
  between whoever built `orchestrator` and the `shared.contracts`
  convention `grid_engine`/`agent_engines` actually used. Fixed across
  all 7 affected files.

## Testing summary

| Codebase | How to run | Coverage |
|---|---|---|
| `agent_engines_rs` | `cargo test -p agent_engines_rs` | 17 unit tests: every agent's accept/reject logic, merit-order clearing, fairness drift, settlement price correctness (including the partial-approval case) |
| `grid_engine` | `python -m pytest grid_engine/tests/ -v` | Real power-flow validation (capped/infeasible/converged cases), fault injection, islanding |
| `orchestrator` | No dedicated test suite yet | Verified manually: real fault injection end-to-end, real WebSocket data shape confirmed against every dashboard component |
| `dashboard` | `npm run build` | Build succeeds and produces valid Vercel output; WebSocket data shape verified against a live orchestrator run, not yet visually confirmed in an actual browser session by this assistant |
