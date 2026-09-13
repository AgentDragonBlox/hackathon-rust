<script lang="ts">
	import { orchestrator, controlReplay } from '$lib/orchestrator.svelte';
	import { AGENT_ENGINE_URL } from '$lib/api';
	import { onMount } from 'svelte';
	const source = $derived(orchestrator.data?.data_source);
	let busy = $state(false);
	let error = $state('');
	let engine = $state('Checking agent engine…');
	async function checkEngine() {
		try {
			const response = await fetch(`${AGENT_ENGINE_URL}/health`, { signal: AbortSignal.timeout(3000) });
			if (!response.ok) throw new Error('Unavailable');
			const health = await response.json();
			engine = health.engine === 'rust' ? 'Rust agent engine · connected' : 'Agent service · Rust identity unverified';
		} catch { engine = 'Agent engine · unreachable'; }
	}
	onMount(() => {
		void checkEngine();
		const timer = setInterval(checkEngine, 10000);
		return () => clearInterval(timer);
	});
	async function control(action: 'play' | 'pause' | 'step' | 'restart') {
		busy = true;
		error = '';
		try { await controlReplay(action); }
		catch (cause) { error = cause instanceof Error ? cause.message : 'Control failed'; }
		finally { busy = false; }
	}
</script>

<section aria-label="Public dataset playback">
	<div class="heading">
		<div>
			<strong>{source?.mode === 'public_dataset_replay' ? 'Public data · historical replay' : source?.mode === 'mock' ? 'Mock data' : source?.mode === 'synthetic_baseline' ? 'Synthetic baseline' : 'Waiting for data source'}</strong>
			<p>{orchestrator.data?.agent_source === 'mock' ? 'Mock agents (explicit test mode)' : engine} · Python AC power-flow validation</p>
		</div>
		{#if source?.mode === 'public_dataset_replay'}
			<div class="controls">
				<button disabled={busy || !orchestrator.connected} onclick={() => control(source?.playing ? 'pause' : 'play')}>{source.playing ? 'Pause' : 'Play'}</button>
				<button disabled={busy || !orchestrator.connected} onclick={() => control('step')}>Step 1 hour</button>
				<button disabled={busy || !orchestrator.connected} onclick={() => control('restart')}>Restart week</button>
			</div>
		{/if}
	</div>
	{#if source?.sample}
		<div class="timeline">
			<span>{source.sample.timestamp.replace('T', ' ').replace('Z', ' UTC')}</span>
			<span>Hour {(source.index ?? 0) + 1} / {source.sample_count} · {source.playing ? 'Playing' : 'Paused'}</span>
		</div>
		<progress max={source.sample_count} value={(source.index ?? 0) + 1} aria-label="Replay progress"></progress>
		<div class="readings">
			{#each Object.entries(source.sample.campus_kw) as [name, kw]}
				<span>{name.replace('_kw', '')}: <b>{kw.toFixed(1)} kW</b></span>
			{/each}
		</div>
		<p>Recorded school, office, EV and solar profiles, scaled to campus capacity. Hospital: assumed 380 kW. Battery state and faults: simulated.</p>
		<p><a href={source.source_url} target="_blank" rel="noreferrer">OPSD / CoSSMic · Germany, June 2016</a> · {source.license} · {source.sample.source_interpolated_columns.length ? 'Source includes interpolated readings this hour' : 'No source interpolation flagged this hour'}</p>
		<p>One recorded hour per cycle (at least 2 seconds). Trades are simulated settlements; they do not dispatch physical equipment or change the next recorded sample.</p>
	{/if}
	{#if error}<p role="alert" class="error">{error}</p>{/if}
</section>

<style>
	section { border: 1px solid var(--panel-border); background: var(--panel); border-radius: 8px; padding: 18px; margin-bottom: 20px; }
	.heading, .timeline, .readings, .controls { display: flex; gap: 12px; flex-wrap: wrap; }
	.heading, .timeline { justify-content: space-between; }
	strong { color: var(--green); }
	p { color: var(--text-dim); font-size: 12px; line-height: 1.5; margin: 8px 0 0; }
	.timeline { margin-top: 16px; font-size: 12px; }
	progress { width: 100%; height: 6px; accent-color: var(--green); margin: 10px 0; }
	.readings { gap: 20px; font-size: 12px; text-transform: capitalize; }
	button { background: var(--bg); border: 1px solid var(--panel-border); border-radius: 5px; color: var(--text); padding: 8px 12px; cursor: pointer; }
	button:disabled { opacity: .5; cursor: wait; }
	a { color: var(--blue); }
	.error { color: var(--red); }
</style>
