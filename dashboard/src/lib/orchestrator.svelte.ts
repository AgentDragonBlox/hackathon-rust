// Reactive client for the orchestrator's /ws/dashboard feed. Exposes a
// single reactive object ($state) that any component can read from --
// this is the Svelte 5 rune equivalent of a store, without the extra
// writable()/subscribe() ceremony.

import { env } from '$env/dynamic/public';

const ORCHESTRATOR_HTTP = (env.PUBLIC_ORCHESTRATOR_URL || 'http://localhost:8000').replace(/\/$/, '');
const ORCHESTRATOR_WS = ORCHESTRATOR_HTTP.replace(/^http/, 'ws') + '/ws/dashboard';

export interface OrchestratorSnapshot {
	tick_count: number;
	agent_source: string;
	data_source: {
		mode: string;
		playing: boolean;
		index?: number;
		sample_count?: number;
		sample_minutes?: number;
		source_url?: string;
		license?: string;
		assumptions?: string;
		sample?: {
			timestamp: string;
			campus_kw: Record<string, number>;
			source_interpolated_columns: string[];
		};
	} | null;
	grid_state: {
		timestamp: string;
		assets: Array<{
			asset_id: string;
			asset_type: string;
			current_load_kw: number;
			current_gen_kw: number;
			soc_percent: number | null;
			min_reserve_percent: number | null;
			online: boolean;
		}>;
		feeders: Array<{
			feeder_id: string;
			loading_percent: number;
			connected_assets: string[];
			status: 'normal' | 'warning' | 'overloaded' | 'faulted' | 'islanded';
		}>;
		active_faults: string[];
		islands: string[][];
	} | null;
	trades: Array<{ trade_id: string; seller_id: string; buyer_id: string; kw_amount: number; price: number }>;
	blockchain: Array<{
		tx_id: string;
		tx_type: string;
		payload_ref: string;
		timestamp: string;
		block_number: number | null;
		prev_hash: string | null;
		hash: string | null;
		status: string;
	}>;
	recent_events: Array<{
		event_id: string;
		timestamp: string;
		source: string;
		message: string;
		severity: 'info' | 'warning' | 'error';
	}>;
}

class OrchestratorClient {
	connected = $state(false);
	data = $state<OrchestratorSnapshot | null>(null);
	private ws: WebSocket | null = null;
	private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
	private stopped = true;

	connect() {
		this.stopped = false;
		if (this.ws) return;
		this.ws = new WebSocket(ORCHESTRATOR_WS);

		this.ws.onopen = () => {
			this.connected = true;
		};

		this.ws.onmessage = (event) => {
			try {
				const msg = JSON.parse(event.data);
				if (msg.type === 'state_update') this.data = msg.data;
			} catch { this.ws?.close(); }
		};

		this.ws.onclose = () => {
			this.connected = false;
			this.ws = null;
			// Reconnect after 2s, same interval the original static dashboard used.
			if (!this.stopped) this.reconnectTimer = setTimeout(() => this.connect(), 2000);
		};

		this.ws.onerror = () => {
			this.ws?.close();
		};
	}

	disconnect() {
		this.stopped = true;
		this.connected = false;
		if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
		this.ws?.close();
		this.ws = null;
	}
}

export const orchestrator = new OrchestratorClient();

export async function triggerScenario(name: string): Promise<void> {
	await post(`/scenario/${name}`);
}

export async function resetScenario(): Promise<void> {
	await post('/scenario/reset');
}

async function post(path: string, body?: unknown): Promise<void> {
	const response = await fetch(`${ORCHESTRATOR_HTTP}${path}`, {
		method: 'POST',
		headers: body ? { 'Content-Type': 'application/json' } : undefined,
		body: body ? JSON.stringify(body) : undefined
	});
	if (!response.ok) throw new Error(`Control failed (${response.status}): ${await response.text()}`);
}

export async function controlReplay(action: 'play' | 'pause' | 'step' | 'restart'): Promise<void> {
	await post('/replay/control', { action });
}
