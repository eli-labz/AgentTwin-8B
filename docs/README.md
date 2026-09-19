# MatrAIx Handbook

Documentation for MatrAIx — evaluate products with simulated users across
**Survey**, **Chatbot**, **Web**, and **App**. For install and a short repo
overview, see the project [README](../README.md).

```bash
uv run --with mkdocs-material mkdocs serve   # http://127.0.0.1:8000
```

---

## 1. Start here

**[Quickstart](quickstart.md)** — install → smoke → one persona → batch → Playground.

| Also | Page |
|------|------|
| Job YAML | [Configuration](configuration.md) |
| Agents & API keys | [Environment → Agents](environment/agents.md) |

---

## 2. Three pillars

| Pillar | Path | What it covers |
|--------|------|----------------|
| **Persona** | [`persona/`](persona/README.md) | Schema, 1M coreset, sampling, [pipeline](persona/pipeline.md), [validation](persona/validation.md) |
| **Application** | [`application/`](application/README.md) | Tasks, Playground; **contracts** in [`application/task-spec/`](../application/task-spec/README.md) |
| **Environment** | [`environment/`](environment/README.md) | Matraix Playground runtime, [agents](environment/agents.md), [web modes](environment/web-interaction.md), [scaling](environment/large-scale-runs.md) |

Optional packages: [packages.md](packages.md).

Enterprise control-plane (Phase 0–10, additive): [enterprise/](enterprise/REPOSITORY_AUDIT.md), [API](enterprise/api.md), [console](enterprise/console.md), [reporting](enterprise/reporting.md), [identity](enterprise/identity.md), [population builder](enterprise/population-builder.md), [experiments](enterprise/experiments.md), [runtime](enterprise/runtime.md), [telemetry](enterprise/telemetry.md), [scale](enterprise/scale.md), [packaging](enterprise/packaging.md).

---

## 3. Persona 1M (recommended)

The checked-in `matraix-persona-dev-sample` (~200) is for smoke only. For real
cohorts, strategy coverage, and Playground sampling, import the public 1M coreset:

```bash
huggingface-cli download MatrAIx2026/MatrAIx_Persona_1M_Public_Release \
  --repo-type dataset \
  --local-dir persona/datasets/matraix-persona-1m/release
```

Playground: Dataset → **`matraix-persona-1m`**. CLI: `--dataset persona/datasets/matraix-persona-1m`.  
Details: [Persona](persona/README.md#setup-and-usage).

---

## 4. Author a task

Start from [`application/task-spec/README.md`](../application/task-spec/README.md)
(contracts and type diagrams), then the [task guide](application/task-guide.md).
Copy an `example-*` under `application/tasks/` and smoke it with the
[Quickstart](quickstart.md).

---

## Map

```text
docs/
├── README.md              this page
├── quickstart.md          install → first runs → Playground
├── configuration.md       job recipes
├── persona/               schema · 1M · pipeline · validation
├── application/           tasks overview · task guide · Playground API
├── environment/           Matraix Playground · agents · web modes · scaling
├── enterprise/            AgentTwin Enterprise architecture + /api/v1
├── adr/                   architecture decision records
└── packages.md

application/task-spec/     task contracts and deep-dive notes
application/tasks/         runnable scenarios
persona/ · environment/    data/schema · runtime code
```
