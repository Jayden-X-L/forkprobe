# ForkProbe selection telemetry worker

This Cloudflare Worker stores only an anonymous task category, the compared
Skill names, and the final choice. It never accepts task text, candidate output,
local paths, reasons, or identity fields.

## Local development

```bash
npm install
npx wrangler d1 migrations apply forkprobe-selection-telemetry --local
npm test
npx wrangler dev --local
```

## Deployment

1. Run `npx wrangler d1 create forkprobe-selection-telemetry`.
2. Replace the zero UUID placeholder in `wrangler.jsonc` with the returned database ID.
3. Run `npm run db:migrate:remote`.
4. Run `npm run deploy`.
5. Configure clients with:

```bash
export FORKPROBE_TELEMETRY_ENDPOINT="https://<worker>/v1/selection-events"
```

Set `FORKPROBE_TELEMETRY=0` to force anonymous selection sharing off locally.
