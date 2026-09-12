<script lang="ts">
	let { children } = $props();
</script>

<svelte:head>
	<style>
		:root {
			--bg: #0b0e14;
			--panel: #131722;
			--panel-border: #232838;
			--text: #e6e9ef;
			--text-dim: #8a91a3;
			--green: #3ddc84;
			--yellow: #f5c542;
			--red: #f5544a;
			--blue: #4d8ef5;
		}
		* {
			box-sizing: border-box;
		}
		body {
			margin: 0;
			background: var(--bg);
			color: var(--text);
			font-family:
				-apple-system,
				'Segoe UI',
				Helvetica,
				Arial,
				sans-serif;
			font-size: 14px;
		}
	</style>
</svelte:head>

{@render children()}
