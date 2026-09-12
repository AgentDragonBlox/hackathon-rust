# Agent Engine (Person 2) — Integration Guide

**For:** Person 1 (Grid Engine) and Person 3 (Orchestrator), and their AI
coding assistants.

**If you are an AI assistant reading this file:** you're helping either
Person 1 or Person 3 on a 3-person hackathon team. Person 2's own AI
assistant wrote this document after building AND actually running this
service — every request/response example below was captured from a real
HTTP call against a live server, not written from memory. Treat the
shapes here as ground truth for what this service really does today.
Where something is a known gap or open question, it's labeled as such
below — don't silently paper over those, surface them to your human so
the team can decide together.

---

## What this is

A FastAPI service that decides which flexible loads/assets should give up
power when a feeder is predicted to overload, using merit-order dispatch
(cheapest first) with a fairness penalty against assets that were tapped
recently. All request/response models are `shared/contracts.py` — this
service adds **no new pydantic models of its own** except where noted
below, so anything typed in `contracts.py` is the one source of truth.

**Run it:**
```bash
pip install -r agent_engines/requirements.txt
uvicorn agent_engines.main:app --port 8002 --reload
```
**Verify it's up:** `curl http://localhost:8002/health` → `{"status":"ok"}`

**Verify the full loop works standalone** (no other service needed):
```bash
python -m agent_engines.run_market
```

---

## Routes, in call order

### 1. `POST /agents/offers`
**Request:** `OffersRequest` (from `contracts.py`) — a `GridState` plus a
list of `FlexibilityRequest`.
**Response:** `list[AgentOffer]`

Only assets listed in the relevant feeder's `connected_assets` get asked
for an offer — this is the "target the few who can help, not everyone"
behavior from the pitch. Every asset that's asked gets an entry in the
response, even if it declines (`rejected: true`, with a `rejection_reason`)
— there is no separate "declined" list, it's folded into `AgentOffer`.

**Verified real request** (this exact payload was POSTed and worked):
```json
{
  "grid_state": {
    "timestamp": "2026-09-12T09:42:03.206583",
    "assets": [
      {"asset_id": "hosp-1", "asset_type": "hospital", "current_load_kw": 380.0, "online": true},
      {"asset_id": "acad-1", "asset_type": "academic", "current_load_kw": 240.0, "online": true},
      {"asset_id": "ev-1",   "asset_type": "ev",       "current_load_kw": 20.0,  "online": true},
      {"asset_id": "batt-1", "asset_type": "battery",  "current_load_kw": 0.0, "soc_percent": 73.0, "min_reserve_percent": 20.0, "online": true}
    ],
    "feeders": [
      {"feeder_id": "F2", "loading_percent": 91.0, "connected_assets": ["hosp-1","acad-1","ev-1","batt-1"], "status": "warning"}
    ],
    "predictions": [
      {"feeder_id": "F2", "predicted_overload": true, "eta_seconds": 240.0, "confidence": 0.9}
    ]
  },
  "requests": [
    {"request_id": "req-1", "feeder_id": "F2", "kw_needed": 42.0, "deadline_seconds": 180.0, "reason": "F2 predicted overload in 4 min"}
  ]
}
```
**Verified real response:**
```json
[
  {"offer_id": "...", "request_id": "req-1", "asset_id": "hosp-1", "offer_type": "load_reduction",  "kw_offered": 19.0, "cost": 9.5, "rejected": false},
  {"offer_id": "...", "request_id": "req-1", "asset_id": "acad-1", "offer_type": "hvac_reduction",   "kw_offered": 72.0, "cost": 4.2, "rejected": false},
  {"offer_id": "...", "request_id": "req-1", "asset_id": "ev-1",   "offer_type": "ev_delay",          "kw_offered": 14.0, "cost": 2.8, "rejected": false},
  {"offer_id": "...", "request_id": "req-1", "asset_id": "batt-1", "offer_type": "battery_discharge", "kw_offered": 30.0, "cost": 5.1, "rejected": false}
]
```

### 2. `POST /agents/clear_market`
**Request:** `ClearMarketRequest` (`{"offers": [...]}`, using offers
straight from step 1's response).
**Response:** `list[ProposedAction]`

**⚠️ Statefulness gap — read this before wiring the orchestrator:**
`ClearMarketRequest` has no `kw_needed` field, so this endpoint looks it
up internally from the `FlexibilityRequest` cached during step 1's call.
**This means you must call `/agents/offers` before `/agents/clear_market`
for the same `request_id`, in that order, against the same running
server instance.** If the service restarts between the two calls, or
runs as multiple instances behind a load balancer, this breaks. Fine for
a single-process demo; flag it if your architecture needs otherwise.

**Verified real response** for the request above:
```json
[
  {"action_id": "...", "request_id": "req-1", "asset_id": "ev-1",   "action_type": "ev_delay",       "kw_amount": 14.0, "source_offer_id": "..."},
  {"action_id": "...", "request_id": "req-1", "asset_id": "acad-1", "action_type": "hvac_reduction",  "kw_amount": 28.0, "source_offer_id": "..."}
]
```
Note it only needed 2 of the 4 assets to hit 42 kW — hospital and battery
weren't touched. That's the intended "targeted, not everyone" behavior.

### 3. `POST /agents/settle`
**Request:** `list[ProposedAction]` — **not a contracts.py model**, just a
raw list, since `contracts.py` has no defined shape for this call. Pass
the subset of actions Person 1 has validated as electrically feasible.
**Response:** `list[Trade]`

Looks up each action's price from the offer cached in step 1 (matched via
`source_offer_id`). Returns `404` with a clear message if you call this
for an offer that was never fetched in this server's lifetime — same
call-order caveat as above.

### 4. `POST /agents/reserve` *(not yet used by anything — see below)*
Query params: `asset_id`, `reserved_kw`, `valid_until` (ISO datetime),
`purpose`. Returns a `ReserveContract`. Built to satisfy `contracts.py`'s
stated ownership ("Person 2 produces ReserveContract") but nothing in
the pipeline calls it yet — only relevant if the demo scenario needs an
asset to commit standing reserve capacity rather than an active trade.

### `POST /agents/debug/ev_deadlines`
Not a real domain endpoint — see "Known gaps" below. Body: `{"asset_id": seconds_until_deadline, ...}`.

---

## Known gaps — decide with the team, don't just assume

`AssetState` in `contracts.py` doesn't carry a few numbers this service's
agent logic needs, so **internal assumptions were made instead of
guessing at contract fields nobody agreed on**. Each is clearly commented
in the relevant agent file (`agent_engines/agents/*.py`):

| Gap | Where it's handled now | If you want it fixed properly |
|---|---|---|
| No critical-vs-flexible load split for hospital/academic | Fixed internal fraction (5% / 30% of `current_load_kw`) | Add a real field to `AssetState`, e.g. `flexible_kw` |
| No agent existed for `asset_type="factory"` | **Fixed 2026-09-12** — see `CHANGELOG.md`; `factory_agent.py` now offers `production_flex`, matching grid_engine's existing action-type handling for it | N/A — resolved without a contract change |
| No battery capacity in kWh, only `soc_percent` | Assumes a fixed 100 kWh / 30 kW campus battery | Add `capacity_kwh`, `max_discharge_kw` to `AssetState` |
| No per-EV charging deadline | Tracked in a server-side dict, set via `POST /agents/debug/ev_deadlines` | Add a real deadline field to `AssetState` for EV-type assets |

**Note to Person 1's AI assistant specifically:** if you're generating
`GridState`/`AssetState` objects from a real pandapower model, none of
the three gaps above block you — this service works fine with the
generic fields it has today. Only worry about these if the team decides
to extend `contracts.py`; if so, ping Person 2 before changing the shared
file (per the "don't fork copies" note at the top of `contracts.py`).

**Note to Person 3's AI assistant specifically:** when building
`AgentClient`, implement the three calls in strict order — `offers` →
`clear_market` → `settle` — for a given `request_id`, against the same
base URL, within one run of this service. A minimal working reference:

```python
import httpx

class AgentClient:
    def __init__(self, base_url="http://localhost:8002"):
        self.base_url = base_url

    def get_offers(self, offers_request: dict) -> list[dict]:
        r = httpx.post(f"{self.base_url}/agents/offers", json=offers_request)
        r.raise_for_status()
        return r.json()

    def clear_market(self, offers: list[dict]) -> list[dict]:
        r = httpx.post(f"{self.base_url}/agents/clear_market", json={"offers": offers})
        r.raise_for_status()
        return r.json()

    def settle(self, confirmed_actions: list[dict]) -> list[dict]:
        r = httpx.post(f"{self.base_url}/agents/settle", json=confirmed_actions)
        r.raise_for_status()
        return r.json()
```
This mirrors the exact calls verified working in `agent_engines/run_market.py`
— if in doubt about a shape, that file (and this doc) is the ground truth,
not assumptions from the original `contracts.py` docstring alone.

---

## Confirmed integration status

**⚠️ Re-verified after Person 1's report of a missing `/agents/settle` route
on `origin/agent-engine` — see below before trusting anything past this line.**

Everything in this doc was genuinely tested (real server, real HTTP calls)
at the time it was written — but testing locally and getting the code
into the shared branch are two different things, and the second one
didn't happen the first time around. If you're reading this after that
gap was found: the fix was to re-verify `main.py` actually registers all
six routes, `run_market.py`/`settlement.py`/`reserve.py` aren't empty,
and `agent_engines/requirements.txt` exists — then commit and push all
of it together, in one go, rather than trusting a previous partial push.

**See `INTEGRATION_GUIDELINES.md` at the repo root for the current
whole-system call sequence and cross-service open items** — this section
covers only what's confirmed about this service specifically.

As of the last sync, Person 3's orchestrator runs the loop against this
service and it works. Two bugs caught by testing against this doc's real
payloads instead of assumed ones: `clear_market` needs `{"offers": [...]}`,
not a bare array; and `settle` replaces orchestrator-side ad-hoc `Trade`
construction entirely — always call `/agents/settle`, never build a
`Trade` yourself elsewhere in the pipeline, or you'll get two different
records of the same trade.

**⚠️ Unverified edge case — confirm before calling this fully closed:**
When Person 1's `ValidationResult.adjusted_kw_amount` is set (a partial
approval, not a full one), does the orchestrator rebuild the
`ProposedAction` with the adjusted `kw_amount` *before* calling
`/agents/settle`? Tested and confirmed on this service's side that
`/agents/settle` correctly records whatever `kw_amount` it's given — so
if this is wrong, the bug is in the orchestrator, not here. Still listed
as open in `INTEGRATION_GUIDELINES.md` until Person 3 confirms a test
with an actual partial approval, not just the full-approval happy path.


## Open question for Person 1 — resolved

**Resolved.** Person 1's grid_engine exposes `POST /grid/validate`,
tested against a real 5-bus pandapower network. Full detail (request/
response shapes, the asset ID mapping table, their own known-gaps list)
is in `grid_engine/INTEGRATION.md` — not duplicated here, see
`INTEGRATION_GUIDELINES.md` at the repo root for the whole-system call
sequence and one cross-service shape inconsistency worth knowing about
before wiring the orchestrator (`/grid/validate` wraps its request,
`/agents/settle` here does not).


