# Implementation Roadmap — AgentTwin Enterprise

Each phase must leave the repository **runnable**: `matraix smoke`, existing Harbor recipes, Playground, and the curated pytest list must keep working. Prefer additive modules over rewrites.

## Phase 0 — Audit, architecture, domain foundation *(this change)*

- Inspected repository → [REPOSITORY_AUDIT.md](REPOSITORY_AUDIT.md)
- Target layers + contracts → [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md)
- Domain + IDs → [DOMAIN_MODEL.md](DOMAIN_MODEL.md)
- Security / tenancy → [SECURITY_MODEL.md](SECURITY_MODEL.md)
- ADR-0001 platform boundaries
- `matraix.enterprise` types, in-memory store, schema validation, tenancy tests

**Exit:** docs exist; invalid persona schemas fail; cross-tenant get raises; existing smoke/unit paths still pass.

## Phase 1 — Domain persistence, configuration, APIs

- Repository interfaces + migrations (SQL or equivalent) without business logic in ORM models
- Bind `TenantId` through a versioned `/api/v1` (tenants, populations, personas, experiments) with OpenAPI
- Configuration: tenant settings, secrets abstraction (env/vault), no hard-coded keys
- Do **not** require tenants for legacy `matraix run -c` yet; add an optional wrapper
- CI: include `tests/multitenancy` and schema validation in the curated list if it is still allow-listed

**Exit:** create tenant → org → population → wrap existing YAML persona via API or SDK skeleton.

## Phase 2 — Enterprise personas, org graph, populations

- Persist optional `enterprise` dimensions
- Organizational graph edges: REPORTS_TO, MEMBER_OF, COLLABORATES_WITH, DEPENDS_ON, APPROVES, ESCALATES_TO, SERVES, SUPPLIES, REVIEWS, OWNS_PROCESS, OWNS_SYSTEM
- Population builder: filters, distributions, org structure, drift tests
- Privacy-preserving default: aggregate stats → constraints → synthetic → validate (no silent cloning of identifiable employees)
- Keep Treiver / Full-DAG / 1M coreset as generation backends

**Exit:** a 10k-employee-shaped population can be declared and validated without rewriting `persona/synthesis`.

## Phase 3 — Experiment control plane

- `EnterpriseExperiment` launch record: hypothesis, populations, tasks, models, seed, metrics, governance, retention, `ExecutionBudget`
- Map experiment → existing Harbor job YAML (generate, do not replace `Job`)
- A/B, cohort, model, prompt, policy, latency, accessibility experiment kinds as **metadata**, same runtime
- Estimate cost before run (extend `MATRIX_MAX_COST_USD` beyond host survey/chat)

**Exit:** one experiment id produces a Harbor job and can be re-run with the same seed metadata.

## Phase 4 — Model gateway and policy gateway

- `ModelProvider` / `ModelRequest` / `ModelResponse` / `ModelCapabilities` / `ModelUsage` / `ModelPolicy` beside LiteLLM
- Routing: capability, allow-list, residency, cost, latency, tenant policy
- Every execution through policy (`SANDBOX_ONLY` default)
- Human approval gate for consequential external actions
- Provider-specific code stays out of persona prompt code

**Exit:** forbidden provider or classified data is DENY/SANDBOX; fallback respects policy.

## Phase 5 — Distributed runtime

- Explicit control / data / execution planes
- Worker abstraction: local, Docker, Kubernetes, queues, batch, GPU clusters
- Event-driven pieces (EventBus, SimulationClock, ObservationStream) **additive** to Harbor trials
- Concurrency and rate limits already sketched (`n_concurrent_trials`, pack/shard env vars) — promote to policy

**Exit:** same experiment runs local or remote without changing domain types.

## Phase 6 — Telemetry, metrics, evaluation

- OpenTelemetry-compatible traces (`trace_id`, `tenant_id`, experiment/persona/task, tokens, cost, policy events)
- Hierarchical metrics SDK (step → enterprise) with intervals and segmentation
- Failure taxonomy (PERCEPTION_FAILURE … ENVIRONMENT_FAILURE)
- Evaluation SDK; LLM judges remain supplemental to deterministic verifiers
- Never present synthetic outputs as human research

**Exit:** a result traces to execution, persona, model, seed, code version.

## Phase 7 — Enterprise console

- Evolve Playground navigation: Overview, Organizations, Populations, Personas, Experiments, Tasks, Environments, Models, Evaluations, Analytics, Governance, Audit, Infrastructure, Settings
- Experiment wizard (population → launch)
- Authenticate the API; close open CORS for non-dev

**Exit:** an admin can drive Phase 1–3 flows without YAML-first.

## Phase 8 — Reporting and executive analytics

- Executive dashboard (success, risk, subgroup, cost, confidence) with drill-down
- Report generator: required sections including limitations + recommended human validation
- Export JSON / CSV / HTML / PDF-ready HTML
- Extend existing `matraix results` and Playground PDF — do not discard them

**Exit:** one experiment produces an exportable report with reproducibility metadata.

## Phase 9 — Security and governance hardening

- OIDC / SSO / SCIM patterns, RBAC+ABAC
- Audit log (append-only, separable from telemetry)
- CSRF, secure cookies, rate limits, dependency scanning, least privilege
- Governance reviews: ingestion, retention, model-provider exposure
- See [GOVERNANCE.md](GOVERNANCE.md) and [SECURITY.md](../../SECURITY.md)

**Exit:** tenant isolation demonstrated on API + storage; audit export works.

## Phase 10 — Performance testing and production packaging

- Benchmarks: personas/sec, tasks/sec, queue/model/db latency, cost/persona
- Docker Compose + Kubernetes references (cloud-neutral core)
- CI gates: lint, types, unit, integration, security, deps, schema, migrations, image, smoke simulation
- Production packaging; no merge silently breaks example tasks

**Exit:** documented scale path from 10 to 1,000,000+ simulated users without one-machine assumptions.

---

## Ordering constraints

- Do not rewrite Harbor or the persona DAG to get tenancy.
- Do not add cloud fields to `Tenant` / `PersonaId`.
- Do not enable persistent memory by default.
- Do not ship live ERP/CRM writes before policy + approval.

## Next implementation target (after this PR)

**Phase 1 continuation:** persist `matraix.enterprise` through a repository + migration, add `/api/v1/tenants` (and populations/personas) without changing `matraix run` defaults, and thread `tenant_id` as optional metadata on generated job sidecars.
