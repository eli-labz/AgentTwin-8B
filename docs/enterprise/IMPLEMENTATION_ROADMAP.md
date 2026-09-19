# Implementation Roadmap — AgentTwin Enterprise

Each phase must leave the repository **runnable**: `matraix smoke`, existing Harbor recipes, Playground, and the curated pytest list must keep working. Prefer additive modules over rewrites.

## Phase 0 — Audit, architecture, domain foundation *(accepted)*

- Inspected repository → [REPOSITORY_AUDIT.md](REPOSITORY_AUDIT.md)
- Target layers + contracts → [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md)
- Domain + IDs → [DOMAIN_MODEL.md](DOMAIN_MODEL.md)
- Security / tenancy → [SECURITY_MODEL.md](SECURITY_MODEL.md)
- ADR-0001 platform boundaries
- `matraix.enterprise` types, in-memory store, schema validation, tenancy tests

**Exit:** docs exist; invalid persona schemas fail; cross-tenant get raises; existing smoke/unit paths still pass.

## Phase 1 — Domain persistence, configuration, APIs *(accepted)*

- Repository protocol `EnterpriseRepository` + SQLite migrations (`matraix.enterprise.migrations`); in-memory store remains the default / fallback
- Versioned `/api/v1` for tenants, organizations, populations, personas with FastAPI OpenAPI (`docs/enterprise/api.md`); `matraix enterprise-api`
- Configuration: `MATRIX_ENTERPRISE_STORE`, `MATRIX_ENTERPRISE_DB`, optional `MATRIX_ENTERPRISE_API_TOKEN` (env only)
- Legacy `matraix run -c` unchanged; optional `--tenant-id` on `generate_application_job.py` writes sidecar metadata only
- Tests: SQLite round-trips, API authz / tenant isolation, existing enterprise + smoke paths

**Exit:** create tenant → default org → population → wrap existing YAML persona via API or repository.

## Phase 2 — Enterprise personas, org graph, populations *(accepted)*

- Organizational graph edges persisted on `EnterpriseRepository` / SQLite (in-memory fallback)
- Population builder declares counts/segments/constraints and names Treiver / Full-DAG / 1M backends — does not rewrite `persona/synthesis`
- Privacy-preserving default: `aggregate_stats_then_synthetic` (no identifiable employee cloning)
- `/api/v1/org-edges` and `/api/v1/population-declarations`
- Tests: graph CRUD + isolation, 10k declaration validation, existing enterprise/API/smoke

**Exit:** a 10k-employee-shaped population can be declared and validated without rewriting `persona/synthesis`.

## Phase 3 — Experiment control plane *(accepted)*

- `EnterpriseExperiment` (`Experiment`) launch record: hypothesis, populations, task, model, seed, metrics, governance, retention, `ExecutionBudget`
- Map experiment → existing Harbor job YAML document (`map_experiment_to_harbor_job`) — does **not** replace `harbor.Job`
- Experiment kinds (`ab`, `cohort`, `model`, `prompt`, `policy`, `latency`, `accessibility`) are metadata; same runtime
- Pre-run cost estimate (`estimate_experiment_cost`) using overridable token/USD rates; gates against budget and optional `MATRIX_MAX_COST_USD` without changing `matraix run` defaults
- `/api/v1/experiments`, `/estimate`, `/harbor-job`; SQLite `launch_json` migration; in-memory fallback

**Exit:** one experiment id produces a Harbor job document and can be re-run with the same seed metadata.

## Phase 4 — Model gateway and policy gateway *(accepted)*

- `ModelProvider` / `ModelRequest` / `ModelResponse` / `ModelCapabilities` / `ModelUsage` / `ModelPolicy` beside LiteLLM ([model-gateway.md](model-gateway.md))
- Routing: capability, allow-list, residency, cost, latency, task complexity, tenant policy
- Every execution through `evaluate_policy` (`SANDBOX_ONLY` default)
- Human approval gate for consequential external actions (`ALLOW_WITH_APPROVAL`)
- Provider-specific code stays out of persona prompt code; LiteLLM is not imported here
- `/api/v1/policy/evaluate`, `/api/v1/models/route|complete|catalog`, `/api/v1/model-policy`

**Exit:** forbidden provider or classified data is DENY/SANDBOX; fallback respects policy.

## Phase 5 — Distributed runtime *(accepted)*

- Explicit control / data / execution planes ([runtime.md](runtime.md))
- Worker abstraction: **local** sandbox path; Docker / Kubernetes / queue / batch named stubs
- Event-driven pieces (`EventBus`, `SimulationClock`, `WorldState`) **additive** to Harbor trials
- Same `Experiment` type submitted to every worker; local path maps a Harbor job **document** and does not construct `harbor.Job`
- `/api/v1/workers`, `/api/v1/experiments/{id}/execute`, `/api/v1/executions`, `/api/v1/events`
- Concurrency stays on `ExecutionBudget.max_concurrency` (copied onto the record)

**Exit:** same experiment runs local or remote without changing domain types.

## Phase 6 — Telemetry, metrics, evaluation *(accepted)*

- OpenTelemetry-compatible traces (`trace_id`, `tenant_id`, experiment/persona/task, tokens, cost, policy events) — no OTel SDK import ([telemetry.md](telemetry.md))
- Hierarchical metrics SDK (step → enterprise) with intervals, segmentation, provenance
- Failure taxonomy (PERCEPTION_FAILURE … ENVIRONMENT_FAILURE)
- Evaluation SDK; LLM judges remain supplemental to deterministic verifiers
- Never present synthetic outputs as human research (`synthetic_equivalent_to_human_research: false`)
- Wired into the local sandbox worker as artifacts (`trace` / `metrics` / `evaluation` / `failure`) and `/api/v1` telemetry routes

**Exit:** a result traces to execution, persona, model, seed, code version.

## Phase 7 — Enterprise console *(accepted)*

- Evolve Playground with an Enterprise mode: Overview, Organizations, Populations, Personas, Experiments, Tasks, Environments, Models, Evaluations, Analytics, Governance, Audit, Infrastructure, Settings ([console.md](console.md))
- Experiment wizard: Population → Scenario → Task → Environment → AI System → Metrics → Governance → Scale → Cost → Launch
- Authenticate `/api/v1` (Bearer + `MATRIX_ENTERPRISE_REQUIRE_AUTH` / production); close CORS for non-dev
- Consume Phase 6 artifacts; surface synthetic ≠ human research in UI copy
- `/console` served by `matraix enterprise-api`; stubs for unfinished deep pages

**Exit:** an admin can drive Phase 1–3 flows without YAML-first.

## Phase 8 — Reporting and executive analytics *(accepted)*

- Executive views from Phase 6 artifacts: success, risk, subgroup, cost, confidence, drill-down ([reporting.md](reporting.md))
- Report generator always includes limitations + recommended human validation; `synthetic_equivalent_to_human_research: false`
- Export JSON / CSV / PDF-ready HTML via `/api/v1/executions/{id}/report` and `/api/v1/experiments/{id}/report`
- Console Analytics (Playground Enterprise + `/console`) consumes those reports
- `matraix results` gains `html` plus limitation notes; Playground jsPDF footer states synthetic ≠ human research

**Exit:** one experiment produces an exportable report with reproducibility metadata.

## Phase 9 — Security and governance hardening *(accepted)*

- OIDC / SSO pattern + SCIM-shaped user hooks without a live IdP ([identity.md](identity.md))
- RBAC (+ ABAC tenant / classification hooks) on `/api/v1` when a principal is authenticated
- Append-only audit log (SQLite triggers; not telemetry) + `/audit/export`
- CSRF on cookie sessions; HttpOnly / SameSite / Secure cookie defaults; rate limits on sensitive routes
- Governance review records for ingestion, retention, model-provider exposure
- See [GOVERNANCE.md](GOVERNANCE.md) and [SECURITY.md](../../SECURITY.md)

**Exit:** tenant isolation demonstrated on API + storage; audit export works.

## Phase 10 — Performance testing and production packaging *(this change)*

- Synthetic bench `matraix enterprise-bench` (`EnterpriseBenchmark.v1`): personas/sec, tasks/sec, queue/model/db latency, telemetry overhead, RSS/CPU, cost/persona and cost/task — sandbox model, CI-sized defaults ([scale.md](scale.md))
- Docker Compose + kustomize references under `deploy/enterprise/` — cloud-neutral core, no baked secrets ([packaging.md](packaging.md))
- Additive CI: `.github/workflows/enterprise.yml` + `scripts/enterprise_ci.sh` (lint, compileall+import, unit/integration, security/deps, schema, migrations, container build, `matraix smoke`)
- Scale path 10 → 1M+ documented without one-machine assumptions; remote workers remain stubs

**Exit:** references and benches exist and are documented. This is **not** a certified 1M soak or a wired Kubernetes worker farm.

---

## Ordering constraints

- Do not rewrite Harbor or the persona DAG to get tenancy.
- Do not add cloud fields to `Tenant` / `PersonaId`.
- Do not enable persistent memory by default.
- Do not ship live ERP/CRM writes before policy + approval.

## Next implementation target (after this PR)

**Post-roadmap operations (not a new numbered phase in this document):** wire Docker / Kubernetes / queue / batch workers on the Phase 5 abstraction; live IdP / JWKS (not just HS256 dev tokens); Harbor `jobs/` filesystem tenancy; optional LiteLLM adapter behind the enterprise model gateway; operator-owned shared SQL + object storage; a real multi-worker soak. Keep the core cloud-neutral. Do not treat Compose/kustomize references as a certified cluster.
