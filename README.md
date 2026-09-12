# Rust port — deployment topology

**Read this before deploying anything.** Vercel cannot host the Rust
services themselves — it's built for static sites and serverless/edge
functions that spin up per-request, not long-running processes with
in-memory state. `agent_engines_rs` depends on exactly that kind of
persistent state (the offer cache, the fairness tracker) across its
`/agents/offers` → `/agents/clear_market` → `/agents/settle` sequence.
Putting it on Vercel as-is would silently break that sequence the moment
two calls landed on different serverless instances.

## What goes where

| Component | Deploys to | Why |
|---|---|---|
| `dashboard/` (SvelteKit) | **Vercel** | Static + a few server routes — exactly what Vercel is for. Confirmed: `npm run build` produces real Vercel Build Output API v3 output (`.vercel/output/functions`, `.vercel/output/static`). |
| `gateway/` (Hono) | **Vercel** (edge function) | Thin proxy/BFF only — no state of its own, so it's a legitimate serverless fit. Proxies `/api/grid/*` and `/api/agents/*` to wherever the real Rust services run. |
| `agent_engines_rs/` | **Fly.io / Railway / Render** (or any host that runs a persistent process) | Needs to stay running and keep its in-memory state across requests. |
| `grid_engine` (Rust port, when built) | Same as above | Same reasoning. |
| `orchestrator` (Rust port, when built) | Same as above | Same reasoning. |

## Local development

```bash
# Terminal 1 — the real Rust service
cd agent_engines_rs && cargo run

# Terminal 2 — the Hono gateway (proxies to the Rust service)
cd gateway && npm install && npm run dev
# listens on :8787, proxies /api/agents/* -> :8002, /api/grid/* -> :8001

# Terminal 3 — the Svelte dashboard
cd dashboard && npm install && npm run dev
# set PUBLIC_AGENT_ENGINE_URL / PUBLIC_GRID_ENGINE_URL in dashboard/.env
# if you want the dashboard talking through the gateway instead of
# directly to the Rust services, point those at http://localhost:8787/api
```

## Deploying

1. **Rust services** — push to Fly.io/Railway/Render from their own
   Dockerfile or native buildpack (not included yet — ask if you want one
   scaffolded). Note the public URL each one gets.
2. **`gateway/`** — `vercel deploy` from inside `gateway/`, or connect the
   repo in Vercel's dashboard with `gateway/` as the project root. Set
   `AGENT_ENGINE_URL` / `GRID_ENGINE_URL` in Vercel's Environment
   Variables to the real URLs from step 1.
3. **`dashboard/`** — same process, `dashboard/` as the project root. Set
   `PUBLIC_AGENT_ENGINE_URL` / `PUBLIC_GRID_ENGINE_URL` — or point them at
   the deployed gateway's URL + `/api` if you want same-origin API calls
   through the proxy instead of calling the Rust services directly
   (avoids CORS entirely).

Both `dashboard/` and `gateway/` can be separate Vercel projects from the
same GitHub repo — Vercel lets you set a subdirectory as a project's root,
so there's no need to split this into separate repos.
