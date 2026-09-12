// Vercel entry point. Deploying this file at api/index.ts is what makes
// Vercel pick it up automatically as a serverless/edge function -- no
// extra vercel.json routing config needed for this piece.
import { handle } from 'hono/vercel';

import { app } from '../app';

export const config = {
	runtime: 'edge'
};

export default handle(app);
