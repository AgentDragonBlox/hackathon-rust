<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { orchestrator, triggerScenario, resetScenario } from '$lib/orchestrator.svelte';
	import NetworkTopology from '$lib/NetworkTopology.svelte';
	import SystemStatus from '$lib/SystemStatus.svelte';
	import BlockchainLedger from '$lib/BlockchainLedger.svelte';
	import AgentActivity from '$lib/AgentActivity.svelte';

	const SCENARIOS = [
		{ name: 'solar_drop', label: 'Solar Drop' },
		{ name: 'demand_spike', label: 'Demand Spike' },
		{ name: 'feeder_overload', label: 'Feeder Overload' },
		{ name: 'battery_failure', label: 'Battery Failure' },
		{ name: 'grid_outage', label: 'Grid Outage' },
		{ name: 'line_fault', label: 'Line Fault' }
	];

	let triggering = $state<string | null>(null);

	async function handleTrigger(name: string) {
		triggering = name;
		try {
			await triggerScenario(name);
		} finally {
			triggering = null;
		}
	}

	async function handleReset() {
		triggering = 'reset';
		try {
			await resetScenario();
		} finally {
			triggering = null;
		}
	}

	onMount(() => orchestrator.connect());
	onDestroy(() => orchestrator.disconnect());
</script>

<div style="padding: 16px 24px;">
	<header style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; flex-wrap: wrap; gap: 12px;">
		<div>
			<h1 style="font-size: 20px; margin: 0;">Microgrid Resilience Exchange</h1>
			<p style="color: var(--text-dim); font-size: 12px; margin: 2px 0 0;">
				predictive multi-agent flexibility market · real AC power-flow validation
			</p>
		</div>
		<div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
			{#each SCENARIOS as scenario}
				<button
					onclick={() => handleTrigger(scenario.name)}
					disabled={triggering !== null}
					style="background: var(--panel); border: 1px solid var(--panel-border); color: var(--text); padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 13px;"
				>
					⚡ {scenario.label}
				</button>
			{/each}
			<button
				onclick={handleReset}
				disabled={triggering !== null}
				style="background: var(--panel); border: 1px solid var(--blue); color: var(--blue); padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 13px;"
			>
				↺ Reset
			</button>
			<span
				style="padding: 4px 10px; border-radius: 4px; font-size: 12px; background: {orchestrator.connected
					? 'var(--green)22'
					: 'var(--red)22'}; color: {orchestrator.connected ? 'var(--green)' : 'var(--red)'};"
			>
				● {orchestrator.connected ? 'live' : 'reconnecting…'}
			</span>
		</div>
	</header>

	<div style="display: grid; grid-template-columns: 1fr 320px; gap: 16px;">
		<div style="background: var(--panel); border: 1px solid var(--panel-border); border-radius: 6px; padding: 16px;">
			<h2 style="font-size: 13px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; margin: 0 0 16px;">
				Network Topology
			</h2>
			<NetworkTopology gridState={orchestrator.data?.grid_state ?? null} />
		</div>

		<div style="display: flex; flex-direction: column; gap: 16px;">
			<div style="background: var(--panel); border: 1px solid var(--panel-border); border-radius: 6px; padding: 16px;">
				<h2 style="font-size: 13px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; margin: 0 0 16px;">
					System Status
				</h2>
				<SystemStatus gridState={orchestrator.data?.grid_state ?? null} />
			</div>
			<div style="background: var(--panel); border: 1px solid var(--panel-border); border-radius: 6px; padding: 16px;">
				<h2 style="font-size: 13px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; margin: 0 0 16px;">
					Blockchain Ledger
				</h2>
				<BlockchainLedger blockchain={orchestrator.data?.blockchain ?? []} />
			</div>
		</div>
	</div>

	<div style="background: var(--panel); border: 1px solid var(--panel-border); border-radius: 6px; padding: 16px; margin-top: 16px;">
		<h2 style="font-size: 13px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; margin: 0 0 16px;">
			Agent Activity
		</h2>
		<AgentActivity events={orchestrator.data?.recent_events ?? []} />
	</div>
</div>
