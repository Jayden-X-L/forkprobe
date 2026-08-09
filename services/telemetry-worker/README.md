# ForkProbe selection telemetry worker

This Cloudflare Worker stores only an anonymous task category, the compared
Skill names, and the final choice. It never accepts task text, candidate output,
local paths, reasons, or identity fields.

The event endpoint is limited to 10 writes per minute per network source at the
Cloudflare edge. The source IP is used only as a transient rate-limit key and is
not written to D1.

Official endpoints:

- Events: `https://forkprobe-selection-telemetry.forkprobe-selection-telemetry.workers.dev/v1/selection-events`
- Aggregated stats: `https://forkprobe-selection-telemetry.forkprobe-selection-telemetry.workers.dev/v1/stats?task_type=<task_type>`
- Health: `https://forkprobe-selection-telemetry.forkprobe-selection-telemetry.workers.dev/health`

## Local development

```bash
npm install
npx wrangler d1 migrations apply forkprobe-selection-telemetry --local
npm test
npx wrangler dev --local
```

## Official deployment

The repository configuration is bound to the official production D1 database.

```bash
npm run db:migrate:remote
npm run deploy
```

## Self-hosting

1. Run `npx wrangler d1 create forkprobe-selection-telemetry`.
2. Replace the official database ID in `wrangler.jsonc` with the returned database ID.
3. Run `npm run db:migrate:remote`.
4. Run `npm run deploy`.
5. Configure clients with:

```bash
export FORKPROBE_TELEMETRY_ENDPOINT="https://<worker>/v1/selection-events"
```

Set `FORKPROBE_TELEMETRY=0` to force anonymous selection sharing off locally.
