<script lang="ts">
	import type { OrchestratorSnapshot } from '$lib/orchestrator.svelte';

	let { events }: { events: OrchestratorSnapshot['recent_events'] } = $props();

	function severityColor(severity: string): string {
		if (severity === 'error') return 'var(--red)';
		if (severity === 'warning') return 'var(--yellow)';
		return 'var(--blue)';
	}
	function formatTime(ts: string): string {
		return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
	}
</script>

<div style="display: flex; flex-direction: column; gap: 4px; max-height: 220px; overflow-y: auto; font-size: 13px;">
	{#each events as event}
		<div>
			<span style="color: var(--text-dim);">{formatTime(event.timestamp)}</span>
			<span style="color: {severityColor(event.severity)}; font-weight: bold;"> {event.source}</span>
			<span> {event.message}</span>
		</div>
	{:else}
		<p style="color: var(--text-dim);">No activity yet.</p>
	{/each}
</div>
