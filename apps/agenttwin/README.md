# AgentTwin web app

Standalone control-plane UI for AgentTwin Enterprise. It talks to the
existing `/api/v1` surface. It does **not** replace Playground or Harbor
and does not change `matraix run`.

## Run

```bash
uv run matraix enterprise-api --port 8090
# open http://127.0.0.1:8090/   or   http://127.0.0.1:8090/app/
```

Same-origin with the API. Optional Bearer token in Settings
(`MATRIX_ENTERPRISE_API_TOKEN`). No secrets are baked into this app.

## What is real

Tenant → organization → population (and declaration) → wrap persona →
org-graph edges → identity → audit. Population **materialize** is exposed
and may fail if the evidence pool is missing.

Experiment launch / Harbor trial execution are **not** in this API yet.
Use Playground or `matraix run` for simulation runs.

Synthetic outputs are not human research.
