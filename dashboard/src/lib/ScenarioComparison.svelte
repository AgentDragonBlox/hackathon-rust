<script lang="ts">
	import { onMount } from 'svelte';
	import { env } from '$env/dynamic/public';

	// Task 3/4/5: renders scripts/run_experiment.py's precomputed
	// no-intervention / load-shedding / market-system comparison. This is
	// a STATIC read of orchestrator's GET /experiment/results (which
	// itself just re-reads data/experiment_results.json on every request)
	// -- not a live re-run of the 168-hour comparison on page load.

	const ORCHESTRATOR_HTTP = (env.PUBLIC_ORCHESTRATOR_URL || 'http://localhost:8000').replace(/\/$/, '');

	interface HourRow {
		hour_index: number;
		feeders: Record<string, number>;
	}
	interface ModeSummary {
		overloaded_timesteps: number;
		peak_overload_pct: number;
		critical_demand_served_pct: number;
		unserved_energy_kwh: number;
		curtailed_energy_kwh: number;
		final_battery_soc_percent: number | null;
		decision_latency: { avg_ms: number | null; max_ms: number | null; samples: number };
	}
	interface ExperimentResults {
		metadata: { generated_at: string; scenario: string; mode_c_error: string | null };
		series: {
			no_intervention: HourRow[];
			load_shedding: HourRow[];
			market_system: HourRow[] | null;
		};
		summary: {
			no_intervention: ModeSummary;
			load_shedding: ModeSummary;
			market_system: ModeSummary | null;
		};
	}

	let results = $state<ExperimentResults | null>(null);
	let error = $state('');
	let loading = $state(true);

	onMount(async () => {
		try {
			const res = await fetch(`${ORCHESTRATOR_HTTP}/experiment/results`);
			if (!res.ok) {
				error =
					res.status === 404
						? 'Not generated yet — run `python scripts/run_experiment.py` from the project root, then reload.'
						: `Failed to load comparison (${res.status})`;
			} else {
				results = await res.json();
			}
		} catch (cause) {
			error = cause instanceof Error ? cause.message : 'Failed to load comparison';
		} finally {
			loading = false;
		}
	});

	const MODES = [
		{ key: 'no_intervention', label: 'No Intervention', color: 'var(--red)' },
		{ key: 'load_shedding', label: 'Load Shedding', color: 'var(--yellow)' },
		{ key: 'market_system', label: 'Our System', color: 'var(--green)' }
	] as const satisfies { key: keyof ExperimentResults['summary']; label: string; color: string }[];

	const ROWS = [
		{ label: 'Overloaded timesteps', key: 'overloaded_timesteps' as const, fmt: (v: number) => `${v}` },
		{
			label: 'Peak overload (pts over 100%)',
			key: 'peak_overload_pct' as const,
			fmt: (v: number) => v.toFixed(1)
		},
		{
			label: 'Critical demand served',
			key: 'critical_demand_served_pct' as const,
			fmt: (v: number) => `${v.toFixed(1)}%`
		},
		{ label: 'Unserved energy (kWh)', key: 'unserved_energy_kwh' as const, fmt: (v: number) => v.toFixed(1) },
		{ label: 'Curtailed energy (kWh)', key: 'curtailed_energy_kwh' as const, fmt: (v: number) => v.toFixed(1) },
		{
			label: 'Final battery SOC',
			key: 'final_battery_soc_percent' as const,
			fmt: (v: number) => `${v.toFixed(1)}%`
		}
	];

	function cell(mode: ModeSummary | null, row: (typeof ROWS)[number]): string {
		if (!mode) return 'N/A';
		const value = mode[row.key];
		return value === null || value === undefined ? 'N/A' : row.fmt(value);
	}

	function latencyCell(mode: ModeSummary | null): string {
		if (!mode || mode.decision_latency.avg_ms === null) return 'N/A';
		return `${mode.decision_latency.avg_ms.toFixed(1)} ms`;
	}

	// Chart window: the natural stress event (recorded academic-demand
	// peak at hour 48, see run_experiment.py's metadata.scenario) with a
	// little padding either side, not the full 168-hour week -- that
	// makes the before/after difference visible instead of a flat line.
	const CHART_START = 40;
	const CHART_END = 56;
	const CHART_W = 560;
	const CHART_H = 140;
	const CHART_MAX_PCT = 160;

	function chartPoints(series: HourRow[] | null): string {
		if (!series) return '';
		const slice = series.slice(CHART_START, CHART_END + 1);
		if (slice.length < 2) return '';
		return slice
			.map((row, i) => {
				const x = (i / (slice.length - 1)) * CHART_W;
				const pct = Math.min(row.feeders.F2 ?? 0, CHART_MAX_PCT);
				const y = CHART_H - (pct / CHART_MAX_PCT) * CHART_H;
				return `${x.toFixed(1)},${y.toFixed(1)}`;
			})
			.join(' ');
	}

	const thresholdY = $derived(CHART_H - (100 / CHART_MAX_PCT) * CHART_H);
</script>

<section aria-label="Scenario comparison">
	<h2>Scenario Comparison — Same Input, Three Strategies</h2>
	<p class="sub">
		Full 168-hour recorded OPSD/CoSSMic replay, identical starting network and input for every
		column. Stress: the recorded academic-demand peak at hour 48 (2016-06-08 06:00 UTC), coinciding
		with near-zero EV load and low solar. No faults injected; nothing is hand-tuned per strategy —
		see README.md's Experimental Evaluation section.
	</p>

	{#if loading}
		<p class="sub">Loading comparison…</p>
	{:else if error}
		<p role="alert" class="error">{error}</p>
	{:else if results}
		<div class="table-wrap">
			<table>
				<thead>
					<tr>
						<th>Metric</th>
						{#each MODES as mode (mode.key)}
							<th style="color: {mode.color}">{mode.label}</th>
						{/each}
					</tr>
				</thead>
				<tbody>
					{#each ROWS as row (row.key)}
						<tr>
							<td>{row.label}</td>
							{#each MODES as mode (mode.key)}
								<td>{cell(results.summary[mode.key], row)}</td>
							{/each}
						</tr>
					{/each}
					<tr>
						<td>Decision latency (avg)</td>
						{#each MODES as mode (mode.key)}
							<td>{latencyCell(results.summary[mode.key])}</td>
						{/each}
					</tr>
				</tbody>
			</table>
		</div>

		<div class="chart-wrap">
			<div class="chart-title">
				Feeder F2 loading, hours {CHART_START}–{CHART_END} (the natural stress window)
			</div>
			<svg
				viewBox="0 0 {CHART_W} {CHART_H + 10}"
				width="100%"
				height="150"
				role="img"
				aria-label="F2 loading percent over the stress window, one line per strategy"
			>
				<line x1="0" y1={thresholdY} x2={CHART_W} y2={thresholdY} stroke="var(--panel-border)" stroke-dasharray="4 3" />
				<text x="4" y={thresholdY - 4} fill="var(--text-dim)" font-size="10">100% limit</text>
				{#each MODES as mode (mode.key)}
					{#if results.series[mode.key]}
						<polyline
							points={chartPoints(results.series[mode.key])}
							fill="none"
							stroke={mode.color}
							stroke-width="2"
						/>
					{/if}
				{/each}
			</svg>
			<div class="legend">
				{#each MODES as mode (mode.key)}
					<span><i style="background: {mode.color}"></i>{mode.label}</span>
				{/each}
			</div>
		</div>

		{#if results.metadata.mode_c_error}
			<p role="alert" class="error">"Our System" column unavailable: {results.metadata.mode_c_error}</p>
		{/if}
		<p class="sub">Generated {results.metadata.generated_at} by scripts/run_experiment.py — rerun it to refresh.</p>
	{/if}
</section>

<style>
	section {
		border: 1px solid var(--panel-border);
		background: var(--panel);
		border-radius: 8px;
		padding: 18px;
		margin-bottom: 20px;
	}
	h2 {
		font-size: 15px;
		margin: 0 0 6px;
	}
	.sub {
		color: var(--text-dim);
		font-size: 12px;
		line-height: 1.5;
		margin: 4px 0 12px;
	}
	.table-wrap {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 12px;
		margin-bottom: 16px;
		min-width: 420px;
	}
	th,
	td {
		text-align: right;
		padding: 6px 8px;
		border-bottom: 1px solid var(--panel-border);
		white-space: nowrap;
	}
	th:first-child,
	td:first-child {
		text-align: left;
		color: var(--text-dim);
	}
	.chart-wrap {
		margin-top: 8px;
	}
	.chart-title {
		font-size: 11px;
		color: var(--text-dim);
		margin-bottom: 4px;
	}
	.legend {
		display: flex;
		gap: 16px;
		flex-wrap: wrap;
		font-size: 11px;
		color: var(--text-dim);
		margin-top: 4px;
	}
	.legend i {
		display: inline-block;
		width: 10px;
		height: 10px;
		border-radius: 2px;
		margin-right: 4px;
		vertical-align: middle;
	}
	.error {
		color: var(--red);
		font-size: 12px;
	}
</style>
