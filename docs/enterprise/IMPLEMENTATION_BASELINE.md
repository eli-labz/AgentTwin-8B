# Implementation Baseline — AgentTwin Enterprise

**Baseline date:** 2026-09-23
**Repository:** `eli-labz/AgentTwin-8B` @ `488a296` (`main`)
**Purpose:** record what exists, what is incomplete, and what constrains the enterprise build
*before* Phase 1 changes land. Complements [REPOSITORY_AUDIT.md](REPOSITORY_AUDIT.md), which
audited the pre-enterprise repository; this document audits the repository *after* the
Phase 0–2 commit and records the verified test baseline in this environment.

---

## 1. Verified test baseline (this environment)

| Suite | Command | Result |
|-------|---------|--------|
| Repo-root `tests/` | `pytest tests/` | **994 passed, 3 skipped** (46 s) |
| Curated CI list (backend + packages) | curated list from `.github/workflows/pytest.yml` | **736 passed, 12 skipped, 2 failed** |
| Host survey smoke | `matraix smoke application/tasks/example-survey_product-feedback` | `Smoke: ok` (fake client, $0) |
| Docker smoke (`harbor-smoke-local.yaml`) | — | **not executed**: Docker is unavailable in this sandbox |
| Frontend typecheck | `npm run typecheck` | pass |
| Ruff (CI pin 0.15.20) | `ruff check .` | clean (0 findings) |

The two curated failures are environment artefacts, not regressions:

- `test_harbor_job_service.py::test_get_job_aggregation_reuses_fresh_artifact` — compares
  `st_mtime_ns` of two writes inside one tick on this filesystem.
- `test_harbor_playground.py::test_resolve_repo_root_handles_local_and_container_layouts` —
  asserts `/workspace` but the checkout is bind-mounted at `/data/workspace` here.

Ruff: the repository has **no lint rule selection** (`[tool.ruff]` only sets
`target-version`), so the installed ruff 0.15 default rule set reports thousands of
pre-existing style findings. CI runs `uvx ruff@0.15.20 check .`; enterprise code added by this
build must be clean under that invocation, but pre-existing findings are out of scope.

Toolchain observed: Python 3.12.14, Node 20, `uv` installed via pip, **no Docker**, no `psql`.
An embedded PostgreSQL (`pgserver` 16.2) can be installed via pip and starts in this sandbox,
so PostgreSQL persistence can be tested for real, not only mocked.

---

## 2. Existing functionality (verified by reading code and running tests)

### 2.1 Research platform (unchanged, must keep working)

- **Persona corpus.** 1,290-dimension catalog (`persona/schema/dimensions.json`), dev sample
  of 200 sparse YAML personas (`persona/datasets/matraix-persona-dev-sample`, ~300 keys each),
  1,290-key validation fixtures, optional 1M Parquet coreset (Hugging Face download, not in git).
- **Full-DAG synthesis.** `matraix.persona_generator.generate_persona_pool` samples full
  1,290-dim personas deterministically: 500 rows in 1.6 s including DAG load (≈1,500 rows/s
  after load); identical seed → identical rows (verified).
- **1M sampling.** `backend.service.persona_1m_pool.sample_production_1m` supports filters,
  stratification, portions, seed; capped at `MAX_SAMPLE_SIZE = 10_000` per call.
- **Harbor runtime.** `harbor.job.Job` expands tasks × agents × attempts into trials, runs a
  `TrialQueue`, writes `jobs/<job>/result.json` and per-trial `result.json`,
  `persona_meta.json`, `verifier/structured_output.json`. Job plugins (`BaseJobPlugin`,
  `on_job_start(job)` / `on_job_end(job_result)`) receive the live `Job`, which exposes
  `on_trial_ended(...)` hooks — this is the seam for enterprise trial capture.
- **Job generation.** `matraix.application_job.build_application_job_config` turns a spec
  (persona pool, ids, task, agent, model, concurrency) into Harbor job YAML with one agent
  entry per persona (`kwargs.persona_path`). `--tenant-id` writes sidecar metadata only.
- **Dispatch.** `matraix run -c` → local `harbor run` or `HarborJobService.launch` for
  Modal / GKE (`compute_family`), with `execution_plane` `harbor|remote`.
- **Results.** `matraix results` ledger (text/json/csv, `--group-by` persona dims);
  Playground aggregation + PDF.
- **Survey smoke.** `matraix.smoke` runs the real in-process survey runner with a
  `DeterministicSurveyJsonClient` (no provider). The Harbor agent
  `PersonaJsonSurvey` has **no** such hook: `InprocessSurveyEvalRunner` falls back to
  `build_json_client(model)` which only knows real providers. An additive `smoke/` model
  prefix hook in the agent is required for offline end-to-end Harbor execution tests.
- **Playground.** FastAPI (`backend.api.app`, 45 inline routes, CORS for `localhost:5173`,
  no auth) + Vite/React SPA (URL-state router in `App.tsx`: Persona World, Task Gallery,
  Home, Playground, Runs). i18n: 7 locale packs with **exact key parity enforced by tests**
  and a static checker that forbids dynamic keys.

### 2.2 Enterprise layer already present (Phase 0–2 commit)

Package `matraix.enterprise` (3,491 lines):

| Module | Provides | Status |
|--------|----------|--------|
| `ids.py` | `TenantId`, tenant-scoped `*Id` value objects, prefixed `new_id` | complete for existing kinds |
| `entities.py` | `Tenant`, `Organization`, `Department`, `Team`, `Population`, `EnterprisePersona`, `ExecutionBudget`, `Experiment` (skeleton) | frozen dataclasses; no `created_by`/`updated_at`/`version`/`status`/`provenance` |
| `persona_schema.py` | Legacy YAML parse + optional `enterprise:` block validation | complete |
| `graph.py` | `OrgEdge`, 11 relations, 6 node kinds, endpoint-kind rules | nodes implicit; no `enterprise`/`business_unit`/`role`/`workflow`/`agent` kinds; no `USES_SYSTEM`/`HANDOFF_TO`/`SUPERVISES` |
| `population_builder.py` | `PopulationDeclaration` (segments count/share, catalog-checked filters, privacy mode fail-closed) | **declaration only** — no materialization, no versions, no quality analysis |
| `policy.py` | `PolicyDecision`, `DataClassification`, `PolicyRequest`, `default_simulation_decision` | vocabulary only; no engine |
| `repositories.py` | `EnterpriseRepository` protocol + `InMemoryEnterpriseStore` | tenant checks in store |
| `sqlite_store.py` / `migrations.py` | SQLite backend, 2 migrations, hand-written SQL per entity | no PostgreSQL, no optimistic concurrency |
| `store.py` | `open_enterprise_store` (`MATRIX_ENTERPRISE_STORE=memory|sqlite`) | default is **in-memory** |
| `api.py` | `/api/v1` tenants, organizations, populations, personas, org-edges, population-declarations; `X-Tenant-Id` header; optional single shared bearer token | no principals, no RBAC, no pagination, no request ids, no audit |
| CLI | `matraix enterprise-api` | serve only |
| Tests | `tests/unit/enterprise/*`, `tests/multitenancy/test_cross_tenant_access.py`, `tests/security/test_policy_defaults.py` | 8 files |

Docs: `REPOSITORY_AUDIT.md`, `TARGET_ARCHITECTURE.md`, `DOMAIN_MODEL.md`, `SECURITY_MODEL.md`,
`GOVERNANCE.md`, `IMPLEMENTATION_ROADMAP.md`, `api.md`, `population-builder.md`,
ADR-0001, root `SECURITY.md`.

---

## 3. Incomplete enterprise functionality (gap list against the build brief)

| Brief section | Gap |
|---------------|-----|
| A. Domain model | Missing: Workspace, User, ServiceAccount, Role, RoleBinding, PopulationVersion, PersonaSnapshot, OrganizationalNode, Cohort, ExperimentVersion, EvaluationSuite, Run, Trial, MetricDefinition, MetricObservation, Artifact, DatasetReference, ModelConfiguration, ModelPolicy, ExecutionPolicy, Budget, Quota, Approval, AuditEvent. Common envelope fields absent. |
| B. Persistence | No PostgreSQL; no optimistic concurrency; no transaction helper; in-memory default. |
| C. Identity/security | Single shared token; no principals, roles, service accounts, OIDC, audit stream, secret provider, `THREAT_MODEL.md`, root `GOVERNANCE.md`. |
| D. Population engine | No materialization, versioning, quality analysis, weights, hybrid sources. |
| E. Org graph | No node records, no visualization payload, limited kinds/relations. |
| F. Agent state | Absent. |
| G. Experiment engine | No lifecycle, estimation, quota/budget/policy checks, approvals, Harbor adapter. |
| H. Orchestration | No shards, leases, work queue, retries, dead-letter, checkpoints, rate limits. |
| I. Model gateway | Absent. |
| J. Evaluation/statistics | Only job ledger; no hierarchical metrics, CIs, effect sizes, validity class. |
| K. Simulation runtime | Absent. |
| L. Observability | No tracing, structured logs, correlation ids. |
| M. API/SDK/CLI | Partial API; no SDK; no `matraix enterprise …` commands. |
| N. Console | No enterprise screens. |
| O. Tests | No `tests/e2e`, `tests/performance`, `tests/reproducibility`; no CI gate proving suites are collected. |
| P. Deployment | No compose stack, Dockerfile, k8s manifests, migration command, health/readiness split. |
| Q. ADRs | Only ADR-0001. |

---

## 4. Architectural constraints

1. **Harbor is the execution engine.** Enterprise runs must generate Harbor job configs and
   attach via the job-plugin / trial-hook seam. No parallel trial loop.
2. **Persona YAML on disk is the agent contract** (`kwargs.persona_path`). Materialized
   population versions must therefore produce YAML files; the database stores manifests,
   hashes, weights and quality findings rather than every 1,290-dim record inline.
3. **Explicit setuptools package list** in `pyproject.toml`; every new sub-package must be
   listed or it will not install.
4. **`PYTHONPATH` layering**: `.:environment/runtime:packages/playground/src:application/playground`
   is required for Harbor agents, Playground services and backend imports. Enterprise code
   that touches Playground services must import lazily and degrade when absent.
5. **Frontend i18n parity**: every UI string needs a key in all 7 packs; the check script
   rejects dynamically built keys.
6. **Single-worker Playground registry** — enterprise state must live in the store, not in
   process memory, so the control plane can run multiple replicas.
7. **No Docker here**: Web/OS-app trials cannot be executed in this environment; survey/chat
   host-lane trials can, given a deterministic client.
8. **1M coreset absent here**: coreset materialization must be implemented against the
   existing sampler API and tested with a mocked/parquet-less path plus a clear
   "dataset not installed" error.

---

## 5. Backward-compatibility requirements

- `matraix run | results | smoke | enterprise-api` keep their flags and behaviour.
- `/api/v1` routes and payloads in `docs/enterprise/api.md` keep working, including bare-list
  responses; the shared `MATRIX_ENTERPRISE_API_TOKEN` keeps working as a dev credential.
- `X-Tenant-Id` header remains accepted (now cross-checked against the principal).
- `matraix.enterprise` public names exported in `__init__.py` remain importable.
- `InMemoryEnterpriseStore` and `SqliteEnterpriseStore` keep their current method set.
- SQLite migrations 1–2 are never edited; new schema arrives as new versions.
- Harbor agents, `application/tasks/*`, job recipes, and Playground routes are untouched
  except for additive, env-guarded hooks.
- The Playground SPA keeps its five existing navigation entries; enterprise screens are added
  behind a new entry.

---

## 6. Risks

| Risk | Mitigation in this build |
|------|--------------------------|
| Scope breadth leads to placeholder interfaces | Vertical slices with tests per phase; BUILD_REPORT lists what was executed vs not |
| Cross-tenant leak via a new table | Generic record store keyed by `(tenant_id, id)`; every query carries tenant; dedicated `tests/multitenancy` |
| Secrets in YAML / logs | Secret provider indirection; redaction filter; `tests/security` asserts absence |
| Large populations bloat SQLite | Snapshot manifests + YAML on disk; artifact root configurable; performance tests at 10k |
| Harbor internals change | Only public hooks (`BaseJobPlugin`, `Job.on_trial_ended`, `JobConfig.plugins`) are used |
| No live provider / Docker | Deterministic `smoke/` client for host survey trials; simulated executor for other task types, clearly labelled |
| i18n parity breaks frontend CI | Enterprise UI strings added to all 7 packs; `i18n:check` run locally |

---

## 6a. Data findings discovered while building the population engine

These are properties of the checked-in persona data, not of the enterprise code. They are
recorded because they shape what a population declaration can legitimately ask for.

1. **Off-catalog values in the dev sample.** `persona/schema/dimensions.json` defines
   `age_bracket` as `Under 5 … 65-74, 75-84, 85+`, but 14 of the 200 checked-in dev-sample
   personas carry `age_bracket: 65+`, which is not a catalog value. The population builder
   validates declared filters against the catalog, so a filter on `65+` is correctly rejected
   even though records with that value exist. Unified-Parquet encoding tolerates this by moving
   unknown values into `attribute_overrides`; the YAML pool has no such normalization.
2. **Cross-dimension contradictions are the norm, not the exception.** Measured with the
   repository's own validator (`matraix.persona_consistency.validate_dimensions`): **32%** of
   dev-sample personas (64/200) and **43%** of freshly Full-DAG-sampled personas fail the
   age/life-stage/seniority consistency rules. `generate_persona_pool` stamps assignments
   without re-sampling on a violation. Population quality therefore reports contradictions as a
   **warning carrying this baseline**, never as an error — otherwise every population built from
   the repository's own data would be reported as failed.
3. **Narrow filters exhaust the dev sample quickly.** Some valid catalog values match a single
   persona (`age_bracket: 85+`). Materialization fails loudly with guidance rather than silently
   returning a short population; oversampling with replacement is opt-in and is recorded in the
   version's provenance.

---

## 6b. Measured scale (10,000-persona materialization)

Executed in this environment on 2026-09-23, Full-DAG backend, three share-based segments
(55/30/15), full 1,290-dimension records written to disk.

| Quantity | Measured |
|----------|----------|
| Personas materialized | 10,000 (segments 5,500 / 3,000 / 1,500 — exact) |
| Wall-clock | 450 s single process |
| Throughput | **~22 personas/s** end to end (sample → validate → write YAML → hash → snapshot) |
| Disk | 385 MB personas + 4.9 MB manifest (**~38.5 KB per persona**) |
| Snapshot read (10,000 rows) | < 10 ms (in-memory store) |
| Cohort draw (1,000 of 10,000) | < 10 ms, reproducible for the same seed |
| Duplicate rate | 0.0 |
| Constraint satisfaction | 1.0 |
| Contradiction rate | 0.524 (see finding 2 above) |
| Dimension coverage | 1,290 / 1,290, mean fill 1.000 |

**Do not confuse two different throughputs.** Raw Full-DAG *sampling* runs ~1,500 rows/s
(500 rows in 1.6 s, no file writing). End-to-end *materialization* runs ~22 personas/s, because
writing a 38.5 KB YAML file per persona and hashing it dominates. Capacity planning must use the
end-to-end figure; using the sampling figure would overstate throughput by roughly 68×.

Derived capacity formulas (single process, linear in size — verified linear between 10 and 10,000):

```text
wall_clock_seconds   ≈ population_size / 22
disk_bytes           ≈ population_size × 38_500          # full 1,290-dim records
manifest_bytes       ≈ population_size × 490
```

So a 1,000,000-person population extrapolates to **~12.6 hours and ~38.5 GB** in one process.
That is precisely why population *definition* is decoupled from execution: a million-person
population is materialized in shards or sampled in batches, never required to run at once.
Sharded and object-storage materialization is remaining work (see Known limitations).

---

## 7. Technical debt directly relevant to this build

- Hand-written per-entity SQL in `sqlite_store.py` does not scale to 25+ entities → introduce a
  generic versioned record table for new entities; keep the existing typed tables.
- `api.py` is a single 600-line factory → split into routers under `matraix/enterprise/api/`
  while keeping `matraix.enterprise.api:app` and `create_enterprise_app` importable.
- Existing entities lack envelope fields → add a `RecordMeta` envelope to new entities and
  expose `version`/`updated_at` where the old entities are re-exposed through the API.
- `PolicyDecision` exists without an engine → implement `ExecutionPolicy` and `ModelPolicy`
  evaluation that returns these decisions and audits them.
- Roadmap document numbers phases differently from the build brief → this build follows the
  brief’s Phase 0–8 numbering; the roadmap is updated to point here.
