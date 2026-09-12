import adapter from '@sveltejs/adapter-vercel';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),
	kit: {
		// adapter-vercel is what actually makes `vercel deploy` work correctly
		// for a SvelteKit app -- it builds serverless/edge functions for any
		// server-side routes and static assets for everything else.
		adapter: adapter()
	}
};

export default config;
