# Changelog — agent_engines_rs (Rust rewrite, this fork only)

Same convention as the original Python `agent_engines/CHANGELOG.md`:
newest first, every entry states who (if anyone) needs to act.

---

## 2026-09-12 — Added: 17 unit tests, matching the original Python suite's core coverage

**What changed:** `#[cfg(test)]` modules in each agent file
(`hospital.rs`, `academic.rs`, `ev.rs`, `battery.rs`, `factory.rs`),
`clearing.rs`, and `settlement.rs`. Covers every agent's accept/reject
logic, merit-order dispatch, fairness drift across repeated calls, and
settlement price correctness (including the partial-approval case where
`kw_amount` has already been overwritten by `adjusted_kw_amount`).

**Why:** the Rust rewrite initially had zero automated tests — everything
was verified manually via `curl`. A CI workflow running only `cargo
build` doesn't catch a logic regression; this suite does.

**Honest gap, not yet closed:** no equivalent of the Python suite's
`test_api.py` (11 route-level tests via FastAPI's `TestClient`). Would
need Axum's `tower::ServiceExt::oneshot` — the dev-dependencies for this
are already in `Cargo.toml`, just unused so far.

**Action needed — Person 1 / Person 3:** none — internal test coverage
only, no contract or behavior change.

---

## 2026-09-12 — Fix: `/agents/reserve` used a stricter datetime parser than every other route

**What happened:** `main.rs` had a leftover local `ReserveParams` struct
using chrono's default (strict, timezone-required) datetime parsing,
while every other route used the shared lenient parser (accepts naive
timestamps too, matching pydantic's leniency on the Python side).

**Fixed:** `/agents/reserve` now uses `contracts::ReserveQuery`, which
shares the same `lenient_datetime` deserializer as everything else.
Verified: a naive timestamp (`2026-09-13T00:00:00`, no timezone) is now
accepted here too.

**Action needed:** none — internal consistency fix.

---

## 2026-09-12 — Fix: missing CORS support blocked the browser-based dashboard

**What happened:** every verification of this service up to this point
used `curl` or server-side `httpx`/Node `fetch` — neither is subject to
browser CORS rules. The first real browser request (from the Svelte
dashboard) silently failed with no useful client-side error beyond
"unreachable," because the response had no `Access-Control-Allow-Origin`
header at all.

**Fixed:** added `tower-http`'s `CorsLayer` (permissive, for local dev/
hackathon purposes). Verified: a simulated cross-origin request now
returns `access-control-allow-origin: *`.

**Action needed:** none — this was purely a gap in this service, caught
before it blocked the dashboard integration for long.

---

## 2026-09-12 — Fix: Rust's datetime parsing was stricter than Python's, rejecting valid payloads

**What happened:** `chrono::DateTime<Utc>`'s default serde deserializer
requires an explicit timezone (RFC3339). Python's pydantic — and the
mock data used throughout earlier development — happily accepts naive
timestamps too. A payload that worked against the Python version could
`422` against this one for no reason a caller would expect.

**Fixed:** custom `lenient_datetime` deserializer — tries RFC3339 first,
falls back to a naive datetime assumed as UTC. Applied to every
timestamp field in `contracts.rs`.

**Action needed:** none — internal compatibility fix, no contract change.

---

## 2026-09-12 — Rust rewrite of agent_engines complete, replaces the Python version in this fork

**What changed:** full rewrite of `main.py`, all five agent files,
`clearing.py`, `fairness.py`, `settlement.py`, and `reserve.py` into
Rust (Axum). Same six routes, same JSON shapes, same constants, same
merit-order/fairness algorithm. Verified side-by-side against the
Python version: identical offer amounts, identical clearing decisions,
identical fairness drift, identical rejection messages.

**Why:** this fork exists specifically to explore an all-Rust stack.
`agent_engines` was the safest starting point — pure business logic, no
risky external numerical dependency (unlike `grid_engine`'s reliance on
pandapower).

**What's different from the Python version:** module-level Python
globals (offer cache, fairness tracker, EV deadlines) became one
`Arc<Mutex<AppState>>`; `shared/contracts.py` is mirrored independently
in `contracts.rs` rather than imported, so the two need manual syncing
if the Python contract changes.

**Action needed — Person 1 / Person 3:** none functionally — the wire
contract is unchanged, so nothing on your side needs to change to talk
to this service instead of the Python one. Worth knowing the language
changed, purely for context if you're ever debugging something and
checking the wrong codebase.
