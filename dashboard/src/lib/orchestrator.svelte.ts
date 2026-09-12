// Reactive client for the orchestrator's /ws/dashboard feed. Exposes a
// single reactive object ($state) that any component can read from --
// this is the Svelte 5 rune equivalent of a store, without the extra
// writable()/subscribe() ceremony.

const ORCHESTRATOR_HTTP = import.meta.env.PUBLIC_ORCHESTRATOR_URL ?? 'http://localhost:8000';
const ORCHESTRATOR_WS = ORCHESTRATOR_HTTP.replace(/^http/, 'ws') + '/ws/dashboard';

export interface OrchestratorSnapshot {
	tick_count: number;
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

	connect() {
		if (this.ws) return;
		this.ws = new WebSocket(ORCHESTRATOR_WS);

		this.ws.onopen = () => {
			this.connected = true;
		};

		this.ws.onmessage = (event) => {
			const msg = JSON.parse(event.data);
			if (msg.type === 'state_update') {
				this.data = msg.data;
			}
		};

		this.ws.onclose = () => {
			this.connected = false;
			this.ws = null;
			// Reconnect after 2s, same interval the original static dashboard used.
			this.reconnectTimer = setTimeout(() => this.connect(), 2000);
		};

		this.ws.onerror = () => {
			this.ws?.close();
		};
	}

	disconnect() {
		if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
		this.ws?.close();
		this.ws = null;
	}
}

export const orchestrator = new OrchestratorClient();

export async function triggerScenario(name: string): Promise<void> {
	await fetch(`${ORCHESTRATOR_HTTP}/scenario/${name}`, { method: 'POST' });
}

export async function resetScenario(): Promise<void> {
	await fetch(`${ORCHESTRATOR_HTTP}/scenario/reset`, { method: 'POST' });
}
