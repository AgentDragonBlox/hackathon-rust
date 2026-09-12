<script lang="ts">
	import type { OrchestratorSnapshot } from '$lib/orchestrator.svelte';

	let { blockchain }: { blockchain: OrchestratorSnapshot['blockchain'] } = $props();

	const newestFirst = $derived([...blockchain].reverse());

	function shortHash(h: string | null): string {
		return h ? h.slice(0, 8) : '—';
	}
	function typeLabel(txType: string): string {
		return txType.replace('_', ' ');
	}
	function formatTime(ts: string): string {
		return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
	}
</script>

<div style="display: flex; flex-direction: column; gap: 8px; max-height: 260px; overflow-y: auto;">
	{#each newestFirst as tx}
		<div style="background: var(--panel-border)55; border-radius: 4px; padding: 8px 10px;">
			<div style="display: flex; justify-content: space-between;">
				<span style="font-weight: bold; font-size: 13px;">#{tx.block_number ?? '?'} {typeLabel(tx.tx_type)}</span>
				<span style="color: var(--text-dim); font-size: 11px;">{formatTime(tx.timestamp)}</span>
			</div>
			<div style="color: var(--text-dim); font-size: 11px; font-family: monospace;">
				{shortHash(tx.prev_hash)} → {shortHash(tx.hash)}
			</div>
		</div>
	{:else}
		<p style="color: var(--text-dim); font-size: 13px;">No settled trades yet.</p>
	{/each}
</div>
