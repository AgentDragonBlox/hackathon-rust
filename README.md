# Aegis Grid Exchange
 
A predictive, physics-aware multi-agent resilience and flexibility exchange for commercial and critical-infrastructure microgrids. The system forecasts grid stress before it happens, lets on-site assets (batteries, EV chargers, flexible loads) bid to relieve it through a simulated market, and validates every proposed action against a real AC power-flow model before it's allowed to execute.
 
Built in 24 hours across three services by a three-person team.
 
## Architecture
 
```
                     ┌──────────────────────┐
                     │     orchestrator     │  Person 3
                     │  (drives the loop,   │
                     │  dashboard, chain)   │
                     └───────────┬──────────┘
                     ┌───────────┴─────────────────┐
                     ▼                             ▼
        ┌────────────────────────┐    ┌────────────────────────┐
        │      grid_engine       │    │     agent_engines      │
        │  Person 1 / port 8001  │    │  Person 2 / port 8002  │
        │                        │    │                        │
        │  pandapower AC power   │    │   offer generation,    │
        │   flow, forecasting,   │    │    market clearing,    │
        │   validation, faults   │    │       settlement       │
        └────────────────────────┘    └────────────────────────┘
```
 
**Canonical call sequence** (see `INTEGRATION_GUIDELINES.md` for the full, current version):
 
1. `grid_engine` → `GET /grid/state` — read or predict grid stress
2. `agent_engines` → `POST /agents/offers` — assets submit flexibility offers
3. `agent_engines` → `POST /agents/clear_market` — offers matched into proposed actions
4. `grid_engine` → `POST /grid/validate` — every proposed action checked against a real re-solved power flow
5. Orchestrator keeps only `feasible` results, substituting `adjusted_kw_amount` where set
6. `agent_engines` → `POST /agents/settle` — feasible actions settled into trades
7. Orchestrator builds a `BlockchainTransaction` from the resulting `Trade` list
 
Separately, for demos: `grid_engine` exposes `POST /grid/fault` to inject a simulated event (line fault, grid outage, battery failure, solar drop, demand spike, feeder overload) with real topology-based islanding, and `POST /grid/fault/clear(_all)` to remove it.
 
## Services
 
| Service | Owner | Port | Docs |
|---|---|---|---|
| `grid_engine` | Person 1 | 8001 | [`grid_engine/INTEGRATION.md`](grid_engine/INTEGRATION.md) |
| `agent_engines` | Person 2 | 8002 | `agent_engines/INTEGRATION.md` |
| `orchestrator` | Person 3 | — | *(in progress)* |
 
Start here: [`INTEGRATION_GUIDELINES.md`](INTEGRATION_GUIDELINES.md) — the single cross-service source of truth for how the three services fit together, what's verified working, and what's still open.
 
## Grid engine
 
A 5-bus representative campus microgrid modeled in [pandapower](https://www.pandapower.org/), with real AC power flow (Newton-Raphson) behind every result — nothing here is simulated or faked.
 
```
    UTILITY GRID (11 kV)
          |
      TRANSFORMER  (11/0.415 kV, 1 MVA)
          |
    CAMPUS MAIN BUS (0.415 kV)
      /        |         \
   F1 (x3)   F2 (x1)    F3 (x1)
    |          |            \
 HOSPITAL   ACADEMIC      FACILITY
    |        |    |
  BESS    SOLAR   EV
```
 
Delivered milestones:
 
- **M1** — static 5-bus network + single AC power-flow snapshot
- **M2** — synthetic time-series load/solar profiles
- **M3** — future-state forecasting
- **M4** — predicted-violation and required-relief detection
- **M5** — proposed-action validation against a real re-solved power flow
- **M6** — simulated fault injection with real topology-based islanding (`networkx` connected-components analysis, not a lookup table)
 
98/98 tests passing. Full endpoint documentation, verified example payloads, and known gaps are in [`grid_engine/INTEGRATION.md`](grid_engine/INTEGRATION.md).
 
## Running it locally
 
```bash
git clone https://github.com/andrew-0228/3rd_sem_hacka.git
cd 3rd_sem_hacka
pip install -r requirements.txt
 
# grid_engine
uvicorn grid_engine.api:app --port 8001 --reload
curl http://localhost:8001/health   # -> {"status": "ok"}
 
# run grid_engine's test suite
pytest grid_engine/tests/ -q
```
 
See each service's own `INTEGRATION.md` for its own run/verify instructions and dependencies.
 
## Known gaps
 
Tracked in detail in `INTEGRATION_GUIDELINES.md`; the current headline item:
 
- `POST /agents/settle` and `POST /agents/reserve` are documented but not yet implemented in `agent_engines` — confirmed by running both services together and calling the real canonical sequence end-to-end. Steps 1–5 above are verified working; step 6 onward is currently blocked on that service.
 
## Repo layout
 
```
grid_engine/                 Person 1 — power-flow simulation, forecasting, validation, faults, API
agent_engines/                Person 2 — offers, market clearing, settlement
orchestrator/                    Person 3 — drives the full loop, dashboard, blockchain record (structure TBD)
shared/                          contracts.py — pydantic request/response shapes shared by all three services
INTEGRATION_GUIDELINES.md        cross-service source of truth — read this first
```
 
Each service currently lives on its own branch (`grid-infra`, `agent-engine`, and the orchestrator's) pending a merge into `main`.
