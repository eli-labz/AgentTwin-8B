# Enterprise console

Phase 7 extends Playground with an **AgentTwin Enterprise** mode. The existing
Persona World / Task Gallery / Home / Playground / Runs pill is unchanged.
The console’s **primary nav** is:

Overview · Organizations · Populations · Personas · Experiments · Tasks ·
Environments · Models · Evaluations · Analytics · Governance · Audit ·
Infrastructure · Settings.

## Open it

In Playground (`http://localhost:5173`) click **Enterprise** in the header
(`?mode=enterprise`). The SPA talks to `matraix enterprise-api` (Vite proxies
`/enterprise-api` → `:8090` in dev).

Standalone (same-origin with the control plane):

```bash
uv run matraix enterprise-api --port 8090
# http://127.0.0.1:8090/console
```

## Experiment wizard

Population → Scenario → Task → Environment → AI System → Metrics →
Governance → Scale → Cost → Launch.

Launch creates an `Experiment` and runs the **local sandbox** worker. It does
not construct `harbor.Job`. Default policy remains `SANDBOX_ONLY`.

## Phase 6 artifacts

Evaluations / Analytics load `trace`, `metrics`, `evaluation`, and `failure`
from `/api/v1`. Every page shows:

**Synthetic persona outputs are not equivalent to human research.**
`synthetic_equivalent_to_human_research: false`. Human validation is
recommended.

Tasks, Environments, Governance, Audit, and Infrastructure are placeholders.

## Auth and CORS

- Bearer token: `MATRIX_ENTERPRISE_API_TOKEN` (env only).
- `MATRIX_ENTERPRISE_REQUIRE_AUTH=1` or `MATRIX_ENTERPRISE_ENV=production`
  requires a token even if unset (401).
- CORS: Vite / Playground origins in **dev**. Production is **closed** unless
  `MATRIX_ENTERPRISE_CORS_ORIGINS` is set.
- Playground: `MATRIX_PLAYGROUND_ENV=production` closes its Vite CORS unless
  `MATRIX_PLAYGROUND_CORS_ORIGINS` is set.

`matraix run` defaults are unchanged.
