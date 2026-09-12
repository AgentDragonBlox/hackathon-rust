<script lang="ts">
	import type { OrchestratorSnapshot } from '$lib/orchestrator.svelte';

	let { gridState }: { gridState: OrchestratorSnapshot['grid_state'] } = $props();

	const totalDemand = $derived(gridState?.assets.reduce((sum, a) => sum + a.current_load_kw, 0) ?? 0);
	const renewableGen = $derived(gridState?.assets.reduce((sum, a) => sum + a.current_gen_kw, 0) ?? 0);
	const hospitalBattery = $derived(gridState?.assets.find((a) => a.asset_id === 'batt-1'));

	function statusColor(status: string): string {
		if (status === 'faulted' || status === 'islanded') return 'var(--red)';
		if (status === 'warning' || status === 'overloaded') return 'var(--yellow)';
		return 'var(--green)';
	}
	function statusLabel(status: string): string {
		return status.toUpperCase();
	}
</script>

<div style="display: flex; flex-direction: column; gap: 14px;">
	<div>
		<div style="color: var(--text-dim); font-size: 12px;">Total Demand</div>
		<div style="font-size: 22px; font-weight: bold;">{totalDemand.toFixed(0)} <span style="font-size: 13px; color: var(--text-dim);">kW</span></div>
	</div>
	<div>
		<div style="color: var(--text-dim); font-size: 12px;">Renewable Generation</div>
		<div style="font-size: 22px; font-weight: bold;">{renewableGen.toFixed(0)} <span style="font-size: 13px; color: var(--text-dim);">kW</span></div>
	</div>
	{#if hospitalBattery?.soc_percent != null}
		<div>
			<div style="color: var(--text-dim); font-size: 12px;">Hospital Battery SOC</div>
			<div style="font-size: 22px; font-weight: bold;">{hospitalBattery.soc_percent.toFixed(0)}%</div>
			<div style="background: var(--panel-border); border-radius: 4px; height: 6px; margin-top: 4px;">
				<div style="background: var(--green); height: 100%; border-radius: 4px; width: {hospitalBattery.soc_percent}%;"></div>
			</div>
		</div>
	{/if}

	{#each gridState?.feeders ?? [] as feeder}
		<div>
			<div style="display: flex; justify-content: space-between; align-items: center;">
				<span style="color: var(--text-dim); font-size: 12px;">Feeder {feeder.feeder_id}</span>
				<span style="font-size: 10px; padding: 2px 6px; border-radius: 3px; background: {statusColor(feeder.status)}22; color: {statusColor(feeder.status)};">
					{statusLabel(feeder.status)}
				</span>
			</div>
			<div style="font-size: 18px; font-weight: bold;">
				{feeder.status === 'faulted' || feeder.status === 'islanded' ? '0' : feeder.loading_percent.toFixed(0)}%
			</div>
			<div style="background: var(--panel-border); border-radius: 4px; height: 6px; margin-top: 2px;">
				<div
					style="background: {statusColor(feeder.status)}; height: 100%; border-radius: 4px; width: {Math.min(feeder.loading_percent, 100)}%;"
				></div>
			</div>
		</div>
	{/each}
</div>
