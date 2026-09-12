<script lang="ts">
	import type { OrchestratorSnapshot } from '$lib/orchestrator.svelte';

	let { gridState }: { gridState: OrchestratorSnapshot['grid_state'] } = $props();

	// Fixed layout -- this mirrors the real, known topology (grid_engine's
	// actual 5-bus model), not a generic graph renderer. Asset IDs are
	// stable across the whole system, so hardcoding positions by ID is
	// safe and much simpler than a general force-directed layout for a
	// network that never changes shape.
	const NODE_TYPE_COLOR: Record<string, string> = {
		hospital: '#f5544a',
		academic: '#4d8ef5',
		ev: '#4d8ef5',
		factory: '#a855f7',
		solar: '#f5c542',
		battery: '#3ddc84'
	};

	const STATUS_COLOR: Record<string, string> = {
		normal: '#3ddc84',
		warning: '#f5c542',
		overloaded: '#f5c542',
		faulted: '#f5544a',
		islanded: '#f5544a'
	};

	function assetFor(id: string) {
		return gridState?.assets.find((a) => a.asset_id === id);
	}
	function feederFor(id: string) {
		return gridState?.feeders.find((f) => f.feeder_id === id);
	}
</script>

{#if gridState}
	{@const f1 = feederFor('F1')}
	{@const f2 = feederFor('F2')}
	{@const f3 = feederFor('F3')}
	<svg viewBox="0 0 700 420" style="width: 100%; height: auto;">
		<!-- F1: hosp-1 -> F1 -> batt-1 -->
		<line x1="80" y1="70" x2="80" y2="320" stroke={STATUS_COLOR[f1?.status ?? 'normal']} stroke-width="3" />
		<!-- F2: acad-1 -> F2 <- ev-1, F2 -> solar-1 -->
		<line x1="230" y1="70" x2="330" y2="200" stroke={STATUS_COLOR[f2?.status ?? 'normal']} stroke-width="3" />
		<line x1="430" y1="70" x2="330" y2="200" stroke={STATUS_COLOR[f2?.status ?? 'normal']} stroke-width="3" />
		<line x1="330" y1="200" x2="330" y2="320" stroke={STATUS_COLOR[f2?.status ?? 'normal']} stroke-width="3" />
		<!-- F3: fac-1 -> F3 (nothing else connected -- this is real, not a rendering gap) -->
		<line x1="580" y1="70" x2="580" y2="200" stroke={STATUS_COLOR[f3?.status ?? 'normal']} stroke-width="3" />

		<!-- Feeder junction labels -->
		<circle cx="80" cy="200" r="14" fill="#131722" stroke={STATUS_COLOR[f1?.status ?? 'normal']} stroke-width="2" />
		<text x="80" y="235" fill="#8a91a3" font-size="12" text-anchor="middle">F1 — {f1?.loading_percent.toFixed(0) ?? '?'}%</text>

		<circle cx="330" cy="200" r="14" fill="#131722" stroke={STATUS_COLOR[f2?.status ?? 'normal']} stroke-width="2" />
		<text x="330" y="235" fill="#8a91a3" font-size="12" text-anchor="middle">
			F2 — {f2?.status === 'faulted' ? 'FAULTED' : `${f2?.loading_percent.toFixed(0) ?? '?'}%`}
		</text>

		<circle cx="580" cy="200" r="14" fill="#131722" stroke={STATUS_COLOR[f3?.status ?? 'normal']} stroke-width="2" />
		<text x="580" y="235" fill="#8a91a3" font-size="12" text-anchor="middle">F3 — {f3?.loading_percent.toFixed(0) ?? '?'}%</text>

		<!-- Asset nodes -->
		{#each [{ id: 'hosp-1', label: 'H', x: 80, y: 40 }, { id: 'acad-1', label: 'A', x: 230, y: 40 }, { id: 'ev-1', label: 'EV', x: 430, y: 40 }, { id: 'fac-1', label: 'F', x: 580, y: 40 }, { id: 'batt-1', label: 'B', x: 80, y: 360 }, { id: 'solar-1', label: 'S', x: 330, y: 360 }] as node}
			{@const asset = assetFor(node.id)}
			{@const color = asset?.online === false ? '#f5544a' : (NODE_TYPE_COLOR[asset?.asset_type ?? ''] ?? '#8a91a3')}
			<circle cx={node.x} cy={node.y} r="30" fill="#131722" stroke={color} stroke-width="3" />
			<text x={node.x} y={node.y + 6} fill="#e6e9ef" font-size="16" font-weight="bold" text-anchor="middle">
				{node.label}
			</text>
			<text x={node.x} y={node.y + 50} fill="#8a91a3" font-size="12" text-anchor="middle">{node.id}</text>
		{/each}
	</svg>

	<div style="display: flex; gap: 16px; flex-wrap: wrap; font-size: 11px; color: var(--text-dim); margin-top: 8px;">
		{#each Object.entries({ Hospital: '#f5544a', Academic: '#4d8ef5', 'EV charger': '#4d8ef5', Factory: '#a855f7', Solar: '#f5c542', Battery: '#3ddc84' }) as [label, color]}
			<span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{color};margin-right:4px;"></span>{label}</span>
		{/each}
		{#each Object.entries({ Normal: '#3ddc84', Warning: '#f5c542', 'Faulted/offline': '#f5544a' }) as [label, color]}
			<span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{color};margin-right:4px;"></span>{label}</span>
		{/each}
	</div>
{:else}
	<p style="color: var(--text-dim);">Waiting for grid state…</p>
{/if}
