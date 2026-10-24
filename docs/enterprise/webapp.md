# AgentTwin web app

A same-origin control-plane UI for AgentTwin Enterprise. It talks to the
existing `/api/v1` surface. It does **not** replace Playground or Harbor and
does not change `matraix run`.

Source: [`apps/agenttwin/`](../../apps/agenttwin/). Served by
`matraix enterprise-api` at `/` (307 to `/app/`) and `/app/`.

## Run

```bash
uv run matraix enterprise-api --port 8090
# open http://127.0.0.1:8090/   or   http://127.0.0.1:8090/app/
```

Open-dev (no token configured) is a platform admin. Production should set
`MATRIX_ENTERPRISE_AUTH_MODE=strict` and a real credential. Tokens belong in
Settings or the environment — never in the static files.

Optional durable store:

```bash
export MATRIX_ENTERPRISE_STORE=sqlite
export MATRIX_ENTERPRISE_DB=.enterprise/store.sqlite
uv run matraix enterprise-api --port 8090
```

## What is real on this API

Tenant → default organization → population (and declaration) → wrap persona →
org-graph edges → identity → audit. Population **materialize** is exposed and
may fail if the evidence pool is missing.

Experiment launch and Harbor trial execution are **not** in this API yet. Use
Playground or `matraix run` for simulation runs.

Default policy remains `SANDBOX_ONLY`. Synthetic persona outputs are not human
research (`synthetic_equivalent_to_human_research: false`).
