# Target Architecture — AgentTwin Enterprise

This is the target modular platform. Phase 0 shipped **contracts and a tenant-scoped domain model**. Phase 1 added **persistence + `/api/v1`**. Phase 2 adds **org-graph edges and a population-shape builder**. Harbor, Playground, and the 1,290-dimension persona stack remain the simulation engines.

## Layered platform

```text
┌─────────────────────────────────────────────┐
│ Enterprise Experience Layer                 │
│ Admin / Research / Evaluation / Executive   │
├─────────────────────────────────────────────┤
│ Experiment & Population Control Plane       │
├─────────────────────────────────────────────┤
│ Persona / Organizational Twin Layer         │
├─────────────────────────────────────────────┤
│ Simulation & Agent Runtime                  │
├─────────────────────────────────────────────┤
│ Task / Workflow / Environment Layer         │
├─────────────────────────────────────────────┤
│ Model & Tool Gateway                        │
├─────────────────────────────────────────────┤
│ Policy / Identity / Governance Layer        │
├─────────────────────────────────────────────┤
│ Telemetry / Evaluation / Observability      │
├─────────────────────────────────────────────┤
│ Data / Memory / Artifact Layer              │
├─────────────────────────────────────────────┤
│ Infrastructure / Scheduler / Compute        │
└─────────────────────────────────────────────┘
```

### Mapping to today’s repo

| Target layer | Current implementation (keep) | Enterprise contract (add) |
|--------------|-------------------------------|---------------------------|
| Experience | Playground UI, Harbor Viewer | Versioned console: orgs, experiments, governance, audit |
| Control plane | Playground API + job YAML | Tenant, population, experiment, policy APIs (`/api/v1`) |
| Org twin | 1,290-dim persona + optional enterprise fields | Org graph + tenancy on every record |
| Simulation runtime | Harbor `Job` / `Trial` / `BaseAgent` | Same runtime, tenant context on traces |
| Task / environment | `application/tasks` + Harbor environments | Workflow contracts; sandboxed enterprise adapters |
| Model gateway | LiteLLM + `provider_credentials` | `ModelProvider` / `ModelPolicy` / residency |
| Policy / identity | Task network policy; Harbor GitHub OAuth | RBAC+ABAC, policy decisions, OIDC |
| Telemetry | `JobResult`, structured_output, `matraix results` | OTel, hierarchical metrics, audit log |
| Data / artifacts | `jobs/`, persona datasets, Parquet | Tenant-prefixed stores; encryption-ready |
| Infrastructure | local / Modal / GKE / use.computer | Worker abstraction; cloud-neutral core |

## Layer contracts

Contracts are **interfaces**, not a rewrite. Phase 0 ships the first Python types in `matraix.enterprise`.

### Experience layer

- **In:** authenticated operator session, tenant selection.
- **Out:** navigation to control-plane resources; never talks to model providers directly.
- **Must not** embed Harbor trial loops in React.

### Control plane

- **In:** tenant-scoped commands (create population, define experiment, set budget).
- **Out:** immutable experiment records + launch requests to the execution plane.
- **Must not** import LiteLLM or Docker SDKs.

### Persona / org twin

- **In:** existing persona YAML/Parquet **plus** optional enterprise dimensions.
- **Out:** `EnterprisePersona` bound to `TenantId` / `PopulationId`.
- **Must not** claim psychological equivalence to humans. Fields are simulation parameters.
- **Must not** call model providers.

### Simulation & agent runtime

- **In:** Harbor job YAML (preserved), plus future experiment id / tenant id in metadata.
- **Out:** trials, trajectories, verifier results.
- **Must not** own IAM or billing.

### Task / environment

- **In:** `task.toml` + instruction + verifier (preserved).
- **Out:** environment observations and artifacts.
- **Must not** hard-code a cloud vendor.

### Model & tool gateway

- **In:** `ModelRequest` (capability, policy, residency, budget).
- **Out:** `ModelResponse` + `ModelUsage`.
- **Must not** live inside persona prompt builders.

### Policy / identity / governance

- **In:** `PolicyRequest` (tenant, persona, action, classification, provider).
- **Out:** `PolicyDecision` (`ALLOW`, `DENY`, `ALLOW_WITH_REDACTION`, `ALLOW_WITH_APPROVAL`, `SANDBOX_ONLY`).
- **Default for enterprise tests:** `SANDBOX_ONLY`.
- **Must not** be bypassable by the runtime.

### Telemetry / evaluation

- **In:** trial events.
- **Out:** hierarchical metrics (step → enterprise) with provenance and intervals.
- **Must not** present synthetic users as human research.

### Data / memory / artifacts

- **In:** tenant-scoped writes.
- **Out:** personas, results, traces, reports.
- **Persistent memory:** never silently enabled.

### Infrastructure

- **In:** worker requests with concurrency and budget.
- **Out:** compute on local / container / k8s / batch.
- **Core domain types must stay cloud-neutral.** Modal and GKE remain adapters.

## Separation rules

1. Persona logic must not import a specific provider SDK.
2. Evaluation must not require Playground HTTP.
3. Task execution must not require the frontend.
4. Tenancy is enforced in the domain/store, not only in UI filters.
5. Existing Harbor jobs remain runnable without a tenant (legacy path) until Phase 1 APIs wrap them.

## Phase 0–2 slice actually implemented

- Typed IDs and entities: `src/matraix/enterprise/`
- `EnterpriseRepository` with in-memory (default) and SQLite backends
- Versioned `/api/v1` tenants / organizations / populations / personas (OpenAPI)
- Optional `enterprise` block on persona records
- Optional `tenant_id` on generated job `.meta.json` sidecars
- Org-graph edges + population-shape declarations (Treiver / Full-DAG / 1M named, not rewritten)
- Policy enums + sandbox default
- Tests under `tests/unit/enterprise`, `tests/multitenancy`, `tests/security`

Not yet: experiment launch API, Harbor filesystem tenancy, console navigation.
