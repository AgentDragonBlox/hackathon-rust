// The Hono app itself, kept separate from api/index.ts (the Vercel entry
// point) so the exact same routing logic can run locally via
// @hono/node-server (see dev.ts) without needing `vercel dev` for quick
// iteration. This mirrors the same "one source of truth, multiple entry
// points" pattern used for shared/contracts.py across the Python services.
//
// WHAT THIS ACTUALLY DOES: a thin proxy/BFF (backend-for-frontend) in
// front of the real Rust services. It does NOT reimplement any business
// logic -- agent_engines and grid_engine still make every real decision.
// This exists so the Svelte dashboard can call one same-origin API
// (avoiding CORS) and so a `/api/health` aggregator can exist without
// each Rust service needing to know about the others.

import { Hono } from 'hono';

const AGENT_ENGINE_URL = process.env.AGENT_ENGINE_URL ?? 'http://localhost:8002';
const GRID_ENGINE_URL = process.env.GRID_ENGINE_URL ?? 'http://localhost:8001';

export const app = new Hono().basePath('/api');

app.get('/health', async (c) => {
	const [agentUp, gridUp] = await Promise.all([
		fetch(`${AGENT_ENGINE_URL}/health`)
			.then((r) => r.ok)
			.catch(() => false),
		fetch(`${GRID_ENGINE_URL}/health`)
			.then((r) => r.ok)
			.catch(() => false)
	]);
	return c.json({ agent_engines: agentUp, grid_engine: gridUp });
});

async function proxy(targetBase: string, incoming: Request, stripPrefix: string): Promise<Response> {
	const url = new URL(incoming.url);
	const path = url.pathname.replace(stripPrefix, '') + url.search;
	const hasBody = !['GET', 'HEAD'].includes(incoming.method);

	const res = await fetch(`${targetBase}${path}`, {
		method: incoming.method,
		headers: { 'content-type': incoming.headers.get('content-type') ?? 'application/json' },
		body: hasBody ? await incoming.clone().text() : undefined
	});

	const body = await res.text();
	return new Response(body, {
		status: res.status,
		headers: { 'content-type': res.headers.get('content-type') ?? 'application/json' }
	});
}

// /api/grid/*  -> GRID_ENGINE_URL/*
app.all('/grid/*', (c) => proxy(GRID_ENGINE_URL, c.req.raw, '/api/grid'));

// /api/agents/*  -> AGENT_ENGINE_URL/agents/*  (note: prefix kept, only /api stripped)
app.all('/agents/*', (c) => proxy(AGENT_ENGINE_URL, c.req.raw, '/api'));
