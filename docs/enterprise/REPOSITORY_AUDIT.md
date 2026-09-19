# Repository Audit — AgentTwin-8B / MatrAIx

**Audit date:** 2026-09-19  
**Repository inspected:** `/workspace` (GitHub: `eli-labz/AgentTwin-8B`, default branch `main`)  
**Working product name:** AgentTwin Enterprise  
**Method:** read implementation, schemas, CLIs, tests, and CI. Claims below cite files. Gaps are labeled as absences in code, not speculation about intent.

The public README still brands the project **MatrAIx**. Python package name is `matraix` (`pyproject.toml`). Harbor is the execution runtime. This audit uses those names as they appear in code.

---

## 1. Existing architecture

Three pillars, documented in `docs/README.md` and `docs/environment/README.md`:

```text
persona/datasets/          application/tasks/           environment/
(YAML / Parquet profiles)  (scenario + verifier)        (Harbor runtime + agents)
        │                         │                            │
        └──── persona_path ───────┴──── task path ─────────────┘
                              MatrAIx Playground job YAML
                                      │
                           trial → agent → artifacts
                                      │
                               jobs/<job_name>/
```

| Layer | Path | Role |
|-------|------|------|
| Product CLI | `src/matraix/` | `matraix run` / `results` / `smoke` |
| Playground API + UI | `application/playground/` | Interactive launch, sampling, Runs |
| Harbor runtime | `environment/runtime/harbor/` | Job/trial loop, environments, verifiers |
| Persona agents | `environment/agents/matraix/agents/` | `persona-*` Harbor agents |
| Playground library | `packages/playground/` | Host-native survey/chat, persona catalog, budget |
| Persona corpus | `persona/` | Schema, synthesis, curation, validation probes |
| Task contracts | `application/task-spec/` | Survey / chatbot / web / os-app |
| Viewer | `apps/viewer/` + `harbor.viewer` | Job/trial/trajectory UI via `harbor view` |

There is **no** control-plane / data-plane / execution-plane split in code. Playground FastAPI (`application/playground/backend/api/app.py`) both serves UI APIs and launches jobs. Harbor `Job` (`environment/runtime/harbor/job.py`) is the single trial-batch entrypoint.

There is **no** tenant, organization, experiment, or policy-engine type in the Python tree (repo-wide search for `tenant` / `RBAC` / `OIDC` / `OpenTelemetry` in product code returned no domain matches).

---

## 2. Existing packages

Declared in `pyproject.toml` `[tool.setuptools.packages]`:

- **`harbor`** → `environment/runtime/harbor` (agents, CLI, environments, llms, models, verifier, viewer, auth, db, metrics, storage, …)
- **`matraix`** → `src/matraix` plus `environment/agents/matraix/agents` for `matraix.agents*`

Optional extras: `cloud`, `computer-1`, `cwsandbox`, `daytona`, `e2b`, `gke`, `islo`, `langsmith`, `modal`, `novita`, `runloop`, `tensorlake`, `tinker`, `use-computer`, `viz`, `wandb`.

Sibling installable packages (`docs/packages.md`):

| Package | Path | Role |
|---------|------|------|
| playground | `packages/playground` | Shared host runners, persona load, model client, `MATRIX_MAX_COST_USD` budget |
| rewardkit | `packages/rewardkit` | Folder-based verifier criteria |
| harbor-langsmith | `packages/harbor-langsmith` | LangSmith environment adapter |

Console scripts: `harbor` / `hr` / `hb` → `harbor.cli.main:app`; `matraix` → `matraix.cli:main`.

---

## 3. Existing persona schema

Canonical catalog: `persona/schema/dimensions.json`.

Inspected header:

- `schemaVersion`: `1.0`
- `targetDimensions`: **1290**
- **Actual dimension count: 1290**
- 43 UI `category` tags (not a nested ontology)
- Each dimension: `id`, `label`, `category`, `description`, `values[]`, `index`, `phrase`, `defaultValue`

First core IDs: `age_bracket`, `region`, `gender_identity`, `urbanicity`, `socioeconomic_band`, `primary_language`, `english_proficiency`, `multilingualism`, `register`, `domain`.

Unified Arrow encoding (`persona/post_process/unified_dataset/schema.py`):

- `ATTRIBUTE_COUNT = 1290`
- Packed nibble attributes + null bitmap
- Fields: `source`, `source_row_index`, `source_record_id`, `attributes`, `null_bitmap`, `attribute_overrides`, `has_description`, `descriptions`, `grounding`, `metadata_json`
- Off-catalog values become `attribute_overrides` (not a hard fail at encode time)

Runtime / Playground YAML (checked-in `persona/datasets/matraix-persona-dev-sample/persona_0042.yaml`):

```yaml
persona_id: '0042'
version: '1.0'
source: amazon
display_name: Casey Brooks
dimensions: { role_function: Teaching, ... }
provenance: { parent_pool, hf_repo, origin_persona_id, ... }
```

Synthetic generator rows (`matraix.persona_generator._persona_entry`) emit `persona_id`, `version`, `source: synthetic`, `dimensions` only.

Playground display model (`packages/playground/src/playground/types.py` `Persona`) is a **reduced** card: `id`, `name`, `summary`, `context`, `source`, preferences/dislikes/constraints/goal/communication_style. It is not the 1,290-dim record.

Dev generation uses a **subset** of the catalog (`matraix.persona_consistency.CORE_DEV_DIMENSION_IDS`, index 1–47, plus `cog_*` and selected food dims) — not all 1,290 fields on every YAML.

---

## 4. Persona generation pipeline

Documented in `docs/persona/pipeline.md`. Layout under `persona/`:

| Stage | Path | What it does |
|-------|------|----------------|
| Schema | `persona/schema/` | `dimensions.json`, taxonomy, i18n labels |
| Extraction (Treiver) | `persona/extraction/` | Regex retrieval + optional LLM judge (`Treiver`) |
| Curation | `persona/curation/` | Attribute pool + existing-data (Wiki, Amazon, surveys) |
| Human extraction | `persona/human_extraction/` | vLLM extraction notes / GPU docs |
| Synthesis | `persona/synthesis/` | Full-DAG (`graph/full_dag.json`) → `PersonaForwardSampler` |
| Post-process | `persona/post_process/` | Quality filter, MinHash dedup, unified Parquet, 1M coreset |
| Validation probes | `persona/validation/tasks/` | 10 attributes × 4 envs |
| Scripts | `persona/scripts/` | `generate_dev_personas.py`, `generate_persona_job.py` |
| Datasets | `persona/datasets/` | Dev sample (~200 YAML), 1M Parquet (download), generated pools |

Playground generation and `generate_dev_personas.py` share `matraix.persona_generator`. Constraints: `GENERATE_COUNT_MAX = 5000`, `MAX_FILTER_STRATA = 2048`. Overlay IDs must match `OVERLAY_ID_RE`.

Public 1M coreset is **not** in git; install path is Hugging Face `MatrAIx2026/MatrAIx_Persona_1M_Public_Release` → `persona/datasets/matraix-persona-1m/release`. Docs state 60% human-grounded / 40% synthetic and calibrated UN/World Bank marginals. Human-grounded means derived from a real profile/survey — **not** verified facts (`docs/persona/README.md`).

---

## 5. Persona validation mechanisms

Several independent validators — not one enterprise schema service.

1. **Cross-dimension consistency** — `matraix.persona_consistency.validate_dimensions` (age / life_stage / seniority / years_experience / education). Used by the generator and unit tests (`tests/unit/playground_core/test_persona_generator.py`). Returns error strings; does not raise by itself.
2. **Catalog / DAG support** — `generate_persona_generator.filter_feasible_strata`, Full-DAG `assignment_supported`. Invalid Full-DAG contrast stamps error without resampling (`docs/persona/README.md`).
3. **Unified codec** — wrong column count raises; unknown values become overrides (`AttributeCodec`).
4. **Quality / contradiction / MinHash** — `persona/post_process/` (corpus build, not Playground launch).
5. **Behavioral probes** — `persona/validation/` (10 attrs × survey/chat/web/osapp-linux); LLM judge of positive vs negative personas (`docs/persona/validation.md`).
6. **Grounding job rollup** — `persona/reporting/eval_grounding_job.py` → `persona_grounding_report.json`.
7. **Task contracts** — Harbor `task.toml` pydantic validators; application task tests in `tests/environment/`.

Playground YAML loader (`playground.persona_catalog._load_curated`) **skips** non-dict YAML; it does not fail the process on a bad file.

---

## 6. Application / task architecture

Contracts: `application/task-spec/README.md`. Four types:

| Type | Canonical example | Auto agent |
|------|-------------------|------------|
| Survey | `application/tasks/example-survey_product-feedback` | `persona-json-survey` (host) |
| Chatbot | `application/tasks/example-chat-api_support_chatbot` | `persona-user-sim` (host + sidecar) |
| Web | `application/tasks/example-web-playwright_quote-choice` | path-heuristic web agent (Docker) |
| OS-app | `application/tasks/example-computer-use-linux_note-to-csv` | `persona-computer-1` |

Required task files: `task.toml`, `instruction.md`, `tests/` verifier, `reporting.json`, `persona_strategy.json`. Optional: extra `input/*`, `self_report_schema`.

`task.toml` example (`example-survey_product-feedback`): `version`, `[task].name`, `[metadata]` type/domain/tags, `[verifier]` / `[agent]` timeouts, `[environment].definition`.

Registry: `application/task-spec/manifest.json` + `src/matraix/task_catalog.py` `APPLICATION_TASK_METADATA` (must match `application/tasks/<dirname>/`).

Harbor task model: `harbor.models.task.config.TaskConfig` (network policy, MCP, skills, timeouts). Network modes: `no-network` / `public` / `allowlist`.

Personas are **referenced** (`persona_path=`), never copied into task folders.

---

## 7. Environment / runtime architecture

Harbor `EnvironmentFactory` (`environment/runtime/harbor/environments/factory.py`) registers:

`host`, `apple-container`, `docker`, `daytona`, `e2b`, `gke`, `islo`, `modal`, `runloop`, `langsmith`, `novita`, plus `singularity`, `tensorlake`, `cwsandbox`, `wandb`, `use-computer` (`EnvironmentType`).

Playground compute family (`src/matraix/compute_family.py`): `local` | `modal` | `gcp`.

| Family | Survey / chat | Web / Linux | macOS / iOS |
|--------|---------------|-------------|-------------|
| `local` | This machine (`host`) | Docker | use.computer |
| `modal` | Modal Function | Modal Sandbox | use.computer (pinned) |
| `gcp` | GKE workers | GKE | use.computer (pinned) |

Execution **plane** (`docs/environment/runtime.md`): `harbor` (API/local starts job) vs `remote` (HTTP Remote Runner). Independent of compute family.

Trial artifacts land under `jobs/<job_name>/` (per-trial `agent/`, `verifier/`, `result.json`, `job.log`). Live mosaic via `live_status.json` / Modal Dict / GKE `/tmp/matraix-live.json`.

---

## 8. Agent abstraction

Harbor `BaseAgent` (`harbor.agents.base.BaseAgent`): `setup` / `run`, `name()`, `model_name`, ATIF / Windows flags.

`AgentFactory` (`harbor.agents.factory`) maps `AgentName` to import paths. Built-in Harbor agents (Oracle, Terminus 2, Claude Code, Codex, Gemini CLI, OpenHands, Computer-1, …) plus MatrAIx persona agents:

- `persona-json-survey`, `persona-user-sim`
- `persona-claude-code`, `persona-gemini-cli`, `persona-codex`
- `persona-openhands-sdk`, `persona-browser-use`, `persona-cocoa`, `persona-computer-1`

Persona context budgets: `src/matraix/persona_agent_context.py` (16k profile + 8k task + 48k turn headroom).

Agents are **persona-conditioned LLM wrappers** with task-type tools. There is no `AgentState(attention, workload, …)` simulation object and no organizational multi-actor graph.

---

## 9. Model-provider abstraction

Two layers:

1. **Harbor LLM** — `harbor.llms.base.BaseLLM` / `LLMResponse` / `LLMBackend` (`litellm`, `tinker`). Implementation: `harbor.llms.lite_llm.LiteLLM`.
2. **Credential routing** — `matraix.provider_credentials.resolve_provider_credential` maps `model_name` prefixes to env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DASHSCOPE_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `XAI_API_KEY`, `DEEPSEEK_API_KEY`, `ZAI_API_KEY`). Preflight prints status, **never the secret**.

Playground model list: `application/playground/backend/service/config.py` `PERSONA_MODEL_KNOB_META` (Anthropic, OpenAI, Gemini, xAI, DashScope, OpenRouter, …).

Default persona model: `anthropic/claude-haiku-4-5` (`playground.types.DEFAULT_PERSONA_MODEL`).

Resolution order (`playground.persona_model.resolve_persona_model`): job `model_name` → optional chat env → `MATRIX_PERSONA_MODEL` / `MATRIX_HARBOR_PERSONA_MODEL` → default.

CLI harness agents (`persona-claude-code`, `persona-gemini-cli`, `persona-codex`) remain **vendor-locked** (`docs/environment/agents.md`). There is no `ModelPolicy` / residency / tenant allow-list router.

---

## 10. Telemetry

What exists today:

- Harbor `JobStats` / `JobResult` (`harbor.models.job.result`): trial counts, `n_input_tokens`, `n_cache_tokens`, `n_output_tokens`, `cost_usd`, exception stats, pass@k.
- Per-trial `result.json`, verifier `structured_output.json`, agent logs, optional web traces / screenshots / os-app recordings.
- Playground `llm_usage_view` + `packages/playground` `llm_usage.py` (OpenAI / Anthropic usage adapters).
- Job spend file `_matraix_budget.json` (`playground.budget`) when `MATRIX_MAX_COST_USD` is set.
- `matraix results` rollup (`src/matraix/job_results.py`) — text / JSON / CSV, optional `--group-by` persona fields.
- Optional extras: `wandb`, `langsmith` environments; Harbor `traces` CLI (hidden).
- Harbor viewer auth/status endpoints.

What does **not** exist: OpenTelemetry SDK usage, `trace_id` + `tenant_id` correlation, append-only security audit log separable from job artifacts, enterprise dashboards for queue depth / policy denials.

---

## 11. Verification framework

Harbor `BaseVerifier.verify()` → `VerifierResult` (`harbor.verifier.base`).

Application tasks own `tests/` (shell + optional Python state checks) and emit `verifier/structured_output.json`. Rewardkit (`packages/rewardkit`) provides programmatic criteria (file exists, JSON path, HTTP status, trajectory tools, …) and LLM judges.

Persona-adherence probes use an LLM judge (`docs/persona/validation.md`) — complementary, not the only path. Docs and `application/task-spec` emphasize deterministic checks where possible.

Verifier network policy is separate from the agent (`NetworkPolicy` on task config).

---

## 12. Existing web interfaces

**Playground** (`application/playground/frontend`, Vite + React):

- Shell routes (`App.tsx`): Home, Persona World (store), Task Gallery, Playground cockpit, Runs.
- Cockpit: persona sampling, task rail, lock pipeline, live mosaic, batch report PDF (`exportBatchReportPdf.ts`).
- i18n packs: en-US, zh-Hans, zh-Hant, ja, ko, es, pt-BR.
- API: `backend.api.app:app` on port 8765 (dev). CORS open to `localhost:5173`. **No authentication** (`docs/application/playground-api.md`: “does not require authentication in local development”).
- OpenAPI at `/docs` when the backend is running.

**Harbor Viewer** (`apps/viewer`, React Router): jobs, trials, compare, task definitions. Started with `harbor view ./jobs --dev`. GitHub OAuth via Supabase (`harbor.auth.handler.AuthHandler`, tests in `tests/unit/viewer/test_auth.py`). Unsafe `return_to` is rejected.

There is no Organizations / Governance / Audit / executive console.

---

## 13. CLI interfaces

**`matraix`** (`src/matraix/cli.py`):

| Command | Behavior |
|---------|----------|
| `run -c <job.yaml>` | Local: wrap `harbor run` with `PYTHONPATH` + `MATRIX_*`. Modal/GKE: `HarborJobService` (same as `POST /api/harbor/jobs`). `--max-cost-usd`, `--compute-family`, `--plane`. |
| `results <job>` | Deterministic ledger; `--format text,json,csv`, `--group-by`. |
| `smoke <survey-task>` | Zero-cost fake-client survey; no Docker, no API key. |

**`harbor`** (`harbor.cli.main`): Typer app — `adapter`, `task`, `dataset`, `job`, `trial`, `cache`, `plugins`, `auth`, `leaderboard`, plus hidden `traces`, `sweeps`, `admin`, `analyze`, `view`, publish/download/upload.

**Scripts:** `application/scripts/generate_application_job.py`, `persona/scripts/generate_dev_personas.py`, `persona/scripts/generate_persona_job.py`.

No `agenttwin tenant|population|experiment` commands.

---

## 14. Test architecture

`[tool.pytest.ini_options] testpaths = ["tests"]`. Markers: `asyncio`, `unit` (this change also documents `multitenancy` / `security`).

| Tree | Contents |
|------|----------|
| `tests/unit/` | Harbor models, agents, viewer, matraix CLI/jobs, playground_core |
| `tests/persona/` | Curation, post_process, synthesis |
| `tests/environment/` | Task contracts, recipes, harbor foundation, example smoke file presence |
| `tests/playground_core/` | Display-name helpers |
| Playground backend | `application/playground/backend/tests/` (many files; CI allow-lists a subset) |
| `packages/playground/src/playground/tests/` | Persona catalog, model client, runners |
| `packages/rewardkit/tests/` | Criteria / isolation |

CI (`.github/workflows/pytest.yml`): curated `pytest` invocation — **not** the entire `tests/` tree plus every backend test. Also: retired `personabench` import guard, `uv pip install -e .` and extras.

Other CI: `ruff.yml` (pinned `ruff@0.15.20`), `playground-frontend.yml` (typecheck, i18n, unit, build), `viewer.yml`.

Absent before this phase: `tests/multitenancy`, `tests/security`, `tests/e2e`, `tests/performance`, `tests/reproducibility` as first-class suites.

---

## 15. Configuration model

- **Job YAML** under `configs/jobs/` (`docs/configuration.md`): `job_name`, `jobs_dir`, `n_attempts`, `n_concurrent_trials`, `environment`, `agents[]`, `tasks[]`. Sidecar `.meta.json` for generator metadata / `computeFamily`.
- **Env vars:** `MATRIX_PERSONA_MODEL`, `MATRIX_HARBOR_PERSONA_MODEL`, `MATRIX_CHATBOT_*`, `MATRIX_COMPUTE_FAMILY`, `MATRIX_EXECUTION_PLANE`, `MATRIX_MAX_COST_USD`, `MATRIX_HOST_PACK_CONCURRENCY`, `MATRIX_WEB_PACK_CONCURRENCY`, `MATRIX_SHARD_CONCURRENCY`, `MATRIX_MAX_CONCURRENT_TRIALS`, Modal / GKE live-push knobs, `REMOTE_RUNNER_API_URL` / `REMOTE_RUNNER_API_KEY`.
- **Playground:** `application/playground/.env.local` (documented in README); `backend.service.config.ConfigManager` for in-process RecAI-era knobs still present in comments.
- **Task env:** `task.toml` `[environment]`, Docker compose definitions under `environment/task-environments/`.
- Secrets: process environment or Modal secrets (`matraix-llm`). `python-dotenv` is a dependency. No secrets manager abstraction.

---

## 16. Job execution model

1. Author/generate a Harbor job YAML (Playground, `generate_application_job.py`, or checked-in recipe).
2. `matraix run -c` or `POST /api/harbor/jobs`.
3. `should_dispatch_via_playground` (`src/matraix/job_run.py`): local Harbor vs `HarborJobService.launch`.
4. Harbor `Job` expands tasks × agents × attempts into `TrialConfig`s, runs a `TrialQueue` with `n_concurrent_trials`.
5. Each trial: environment setup → agent `run` → verifier → `TrialResult`.
6. Job writes `JobResult` + filesystem tree. Playground aggregates `aggregation.json` / PDF.

Concurrency: Playground Parallel / `nConcurrentTrials`; host pack default 32; web pack default 1; shard concurrency default 8 (`docs/environment/large-scale-runs.md`). Wait timeout default 4 hours (`MATRIX_RUN_WAIT_TIMEOUT_SEC`).

There is no first-class `EnterpriseExperiment` object, budget estimate-before-run, or governance gate on launch.

---

## 17. Existing security assumptions

Observed in code:

- **Research / single-operator default.** Playground API is unauthenticated. CORS allows local Vite origins.
- **Secrets in environment variables**, not in repo. Credential preflight refuses to print values. Tests use fake `sk-…` strings via `monkeypatch.setenv`.
- **Harbor registry auth** is GitHub OAuth through Supabase (`harbor.auth`, `harbor.db.client`) — for publishing/leaderboard, not Playground tenancy.
- **Task network policy** can lock down agent/verifier egress (`NetworkMode`).
- **Remote Runner** optional bearer `REMOTE_RUNNER_API_KEY`.
- **Viewer** rejects unsafe OAuth `return_to`.
- **No `SECURITY.md`** existed before this phase.
- **No CSRF / session IAM / RBAC / SCIM / OIDC** on Playground.
- Harbor `JobConfig` dumps can redact agent env secrets (`tests/unit/models/test_agent_config_env.py`).

This is a reasonable lab posture. It is not an enterprise isolation model.

---

## 18. Existing scalability assumptions

Documented and implemented:

- One job folder for a whole cohort (do not start one job per persona).
- Local thousands of survey/chat trials via `n_concurrent_trials`.
- Modal / GKE for laptop-close and higher concurrency; shard workers; image cache volumes.
- 1M persona pool with SQLite postings indexes (~2.5GB) for filtered sampling; Playground samples up to 10,000 rows from 1M (`docs/persona/README.md`).
- Synthetic generator caps (5,000 per write, 2,048 strata).
- Context-window budgets so full personas are not truncated.

Not designed in code: million-user **concurrent** simulation as a scheduler product, multi-tenant quotas, Kubernetes-native control plane, GPU inference clusters as a first-class worker type (Tinker extra exists; not wired as enterprise workers).

`docs/environment/large-scale-runs.md` is operational, not a capacity model.

---

## 19. Major technical debt

1. **Naming drift:** MatrAIx / Harbor / Playground / retired `personabench` (CI still greps for it; `task_catalog.py` comments still mention `personabench/` Harbor names).
2. **Playground API module docstring** still describes RecAI / `recbot.interecagent_bridge` (`application/playground/backend/api/app.py`) while the product path is Harbor jobs.
3. **Dual persona models:** 1,290-dim YAML vs reduced `playground.types.Persona` vs packed Parquet vs enterprise-less Harbor `persona_path`.
4. **Explicit setuptools package list** — easy to omit new modules (mitigated for `matraix.enterprise` in this phase).
5. **Curated CI test list** can silently skip new tests if authors only add files under unlisted backend paths (repo-root `tests/` is included via `tests/`).
6. **Vendor-locked CLI agents** vs LiteLLM host agents — two provider stories.
7. **GCP/Modal-specific launch paths** live next to a cloud-neutral Harbor core; domain model must stay provider-agnostic.
8. **In-memory Playground job registry** assumes one uvicorn worker.
9. **Deprecated Harbor dataset `registry` key** still migrated in `JobConfig`.
10. **No migrations / metadata DB** for product entities (Harbor Supabase is registry, not experiment store).

---

## 20. Enterprise-readiness gaps

Mapped to the master build prompt. **Missing in inspected code unless noted.**

| Capability | Gap |
|------------|-----|
| Multi-tenancy | No tenant IDs on jobs, personas, logs, caches, artifacts |
| Org digital twins | No department/team graph; some related **persona dimensions** exist (`role_function`, `seniority`, `company_size`, `risk_tolerance`, `accessibility_needs`, `trust_level`, `time_pressure`) but **not** `department`, `team`, or `tenure` as schema IDs |
| Organizational graph | No REPORTS_TO / ESCALATES_TO / … relationships |
| Experiment engine | Jobs exist; no hypothesis/governance/budget object |
| Population builder (enterprise) | Playground sampling exists; no org-structure + distribution-drift product |
| Cognitive state | No bounded `AgentState` |
| Scoped memory | No persistence policy (ephemeral/session/experiment/persistent) |
| Model gateway policy | Prefix→env var only |
| Policy engine | No ALLOW/DENY/… gate on executions |
| IAM | Playground open; Harbor GitHub OAuth only |
| Data classification | None |
| Privacy-preserving generation | Coreset is public research data; no “aggregate stats → synthetic” enterprise mode |
| Workflow / ERP adapters | Four environment types only |
| Real-time world | Trial loop, not EventBus / SimulationClock |
| Hierarchical evaluation SDK | Job rollup + reporting.json, not Step→Enterprise metrics |
| Traceability / OTel / audit | Partial job artifacts only |
| Control/data/execution planes | Coupled |
| Cost governance | `MATRIX_MAX_COST_USD` for host survey/chat only |
| Enterprise console / exec dashboard / reports | Playground + PDF batch report only |
| Versioned REST `/api/v1` + SDK | Playground `/api/*` unversioned, no `EnterpriseClient` |
| SECURITY.md / GOVERNANCE.md / ADRs | Absent before this phase |
| CI enterprise gates | Lint + curated pytest + frontend; no security/migration/schema gates |

**Preserve:** population-scale personas, persona-conditioned agents, four evaluation environments, cohort sampling, task verification, telemetry/results rollup, Playground, Harbor worker backends.

**Do not replace those.** Add a tenant-scoped domain layer beside them (this phase) and grow APIs/runtime incrementally.
