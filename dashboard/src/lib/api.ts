// API base URLs, configurable per environment so the same build works
// locally, on a Vercel preview, and in production without code changes.
// Set these in .env (local) or Vercel's Environment Variables UI --
// see .env.example.
//
// IMPORTANT: these point at wherever agent_engines/grid_engine/orchestrator
// actually run. Since Vercel can't host the long-running Rust services
// themselves (see README.md's "Deployment topology" section), these will
// point at a separate host (Fly.io/Railway/Render/etc.), not at Vercel.

import { env } from '$env/dynamic/public';

const AGENT_ENGINE_URL = (env.PUBLIC_AGENT_ENGINE_URL || 'http://localhost:8002').replace(/\/$/, '');
const GRID_ENGINE_URL = (env.PUBLIC_GRID_ENGINE_URL || 'http://localhost:8001').replace(/\/$/, '');

export interface AssetState {
	asset_id: string;
	asset_type: string;
	current_load_kw: number;
	current_gen_kw: number;
	soc_percent: number | null;
	min_reserve_percent: number | null;
	online: boolean;
}

export interface NetworkState {
	feeder_id: string;
	loading_percent: number;
	connected_assets: string[];
	status: 'normal' | 'warning' | 'overloaded' | 'faulted' | 'islanded';
}

export interface GridState {
	timestamp: string;
	assets: AssetState[];
	feeders: NetworkState[];
	predictions: unknown[];
	active_faults: string[];
	islands: string[][];
}

export async function fetchGridState(): Promise<GridState> {
	const res = await fetch(`${GRID_ENGINE_URL}/grid/state`);
	if (!res.ok) throw new Error(`grid_engine /grid/state failed: ${res.status}`);
	return res.json();
}

export async function checkHealth(baseUrl: string): Promise<boolean> {
	try {
		const res = await fetch(`${baseUrl}/health`);
		return res.ok;
	} catch {
		return false;
	}
}

export { AGENT_ENGINE_URL, GRID_ENGINE_URL };
