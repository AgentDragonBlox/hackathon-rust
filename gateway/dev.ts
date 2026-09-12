// Local-only dev server. Not deployed -- Vercel uses api/index.ts's edge
// handler instead. This exists purely so `npm run dev` gives a fast local
// loop without needing `vercel dev`.
import { serve } from '@hono/node-server';

import { app } from './app';

const port = Number(process.env.PORT ?? 8787);
console.log(`gateway (dev) listening on :${port}`);
serve({ fetch: app.fetch, port });
