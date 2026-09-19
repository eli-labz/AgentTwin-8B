# Domain Model — AgentTwin Enterprise

## Tenancy hierarchy

```text
Tenant
 └── Organization          (business unit / legal entity; optional parent)
      ├── Department
      │    └── Team
      ├── Population
      ├── Persona          (tenant-owned wrapper around existing YAML)
      └── Experiment       (control-plane skeleton)
```

This is the **isolation and ownership** tree. It does not replace Harbor’s job → trial → verifier tree. A future experiment launch will *reference* populations, tasks, and models; Harbor will still execute trials.

## Immutable IDs

Implemented in `matraix.enterprise.ids`.

| Kind | Type | Scope |
|------|------|--------|
| tenant | `TenantId` | Root. Not nested under another tenant. |
| organization | `OrganizationId` | Embeds `TenantId` |
| department | `DepartmentId` | Embeds `TenantId` |
| team | `TeamId` | Embeds `TenantId` |
| user | `UserId` | Embeds `TenantId` (reserved; IAM in a later phase) |
| persona | `PersonaId` | Embeds `TenantId` |
| population | `PopulationId` | Embeds `TenantId` |
| cohort | `CohortId` | Embeds `TenantId` |
| experiment | `ExperimentId` | Embeds `TenantId` |
| task | `TaskId` | Embeds `TenantId` |
| scenario | `ScenarioId` | Embeds `TenantId` |
| execution | `ExecutionId` | Embeds `TenantId` |
| observation | `ObservationId` | Embeds `TenantId` |
| model | `ModelId` | Embeds `TenantId` |
| policy | `PolicyId` | Embeds `TenantId` |
| artifact | `ArtifactId` | Embeds `TenantId` |

Rules:

- IDs are frozen value objects. Bodies match `^[a-z0-9][a-z0-9_-]{0,127}$`.
- Generated bodies use a kind prefix + 32 hex chars (`new_id`).
- A `PersonaId` from tenant A **does not belong to** tenant B (`belongs_to` is false). Repositories raise `CrossTenantAccessError` rather than returning the foreign row.
- Existing YAML `persona_id: "0042"` is **`legacy_persona_id`**. It is not globally unique and is not an enterprise primary key.

## Entities (Phase 0)

All live in `matraix.enterprise.entities`.

### Tenant

Isolation boundary for every enterprise-owned record. Fields: `id`, `name`, `slug`.

### Organization

Business unit under a tenant. Optional `parent_id` (same tenant only). Optional `industry`, `geography`.

### Department / Team

Named nodes for org-graph edges. Team may point at a department. Cross-tenant foreign keys are rejected at construction.

### Org-graph edges

Directed, tenant-owned relationships (`matraix.enterprise.graph.OrgEdge`). Relations:

`reports_to`, `member_of`, `collaborates_with`, `depends_on`, `approves`, `escalates_to`, `serves`, `supplies`, `reviews`, `owns_process`, `owns_system`.

Endpoints are `persona`, `team`, `department`, `organization`, or labeled `process` / `system` nodes (not first-class entities yet). Both ends are resolved inside the calling tenant. Stores raise `CrossTenantAccessError` on a foreign edge id. `reports_to` cannot be a self-loop. Duplicate `(relation, source, target)` tuples are rejected.

### Population

Named, tenant-owned set with `organization_id`, optional `team_id`, optional `target_size`. Playground datasets still attach here.

### Population builder (declaration)

`build_population_declaration` records a **shape** (target size, segments, filters, constraints, backend name). It does **not** rewrite `persona/synthesis` or emit YAML.

| Field | Meaning |
|-------|---------|
| `target_size` | Declared headcount (10k-scale is a first-class test) |
| `backend` | `treiver` / `full_dag` / `coreset_1m` — existing generation pipelines |
| `segments` | Each has `count` **or** `share`, optional dimension filters |
| `resolved_counts` | Integer split that sums to `target_size` |
| `privacy_mode` | Default `aggregate_stats_then_synthetic`. Cloning identifiable employees is rejected. |
| `include_org_structure` | Hint that org-graph edges should inform a later fill |

Catalog filter values fail closed when the dimension is in `dimensions.json`. Unknown dimension ids are allowed as org overlays (e.g. `department`).

### EnterprisePersona

Wraps the **existing** Playground/Harbor record:

| Field | Source today |
|-------|----------------|
| `legacy_persona_id` | YAML `persona_id` |
| `version` | YAML `version` |
| `source` | YAML `source` |
| `display_name` | YAML `display_name` |
| `dimensions` | YAML `dimensions` (1,290-catalog values when the id is in catalog) |
| `provenance` | YAML `provenance` |

New:

| Field | Meaning |
|-------|---------|
| `id` | Tenant-scoped `PersonaId` |
| `tenant_id` / `organization_id` | Required ownership |
| `population_id` | Optional |
| `enterprise` | Optional `EnterpriseDimensions` |
| `data_classification` | `PUBLIC` … `RESTRICTED` (default `INTERNAL`) |

Factory: `EnterprisePersona.from_legacy_record(...)`. Checked-in `persona_0042.yaml` parses without an `enterprise` block.

### EnterpriseDimensions (optional)

Simulation parameters only — **not** a claim of psychological equivalence.

| Group | Fields |
|-------|--------|
| organization | industry, company_size, operating_model, geography, regulatory_environment |
| employment | department, team, role, seniority, tenure, employment_type, manager_level, reporting_structure |
| capabilities | domain_expertise, technical_skill, ai_literacy, digital_literacy, process_knowledge, product_knowledge |
| behavior | risk_tolerance, change_resistance, autonomy_preference, communication_style, collaboration_preference, escalation_tendency, compliance_orientation |
| work_context | workload, interruption_rate, time_pressure, tool_complexity, information_access, decision_authority |
| accessibility | accommodations, interface_requirements |
| ai_interaction | trust_in_ai, prior_ai_experience, verification_behavior, automation_bias, willingness_to_delegate |

Unknown keys fail validation. Overlap with catalog dimensions (e.g. `seniority`, `risk_tolerance`) is intentional: catalog values stay on `dimensions`; enterprise groups are an explicit overlay for org-twin experiments.

Existing catalog IDs that already help enterprise simulations (do not rename): `role_function`, `seniority`, `company_size`, `risk_tolerance`, `accessibility_needs`, `trust_level`, `time_pressure`, `tech_savviness`. Catalog does **not** currently define `department`, `team`, or `tenure`.

### Experiment / EnterpriseExperiment

Control-plane **launch record** (`Experiment`, alias `EnterpriseExperiment`). Default policy is `SANDBOX_ONLY`. It does **not** replace `harbor.Job`.

| Field | Meaning |
|-------|---------|
| `hypothesis` / `objective` | Why the run exists |
| `population_ids` | Tenant-owned populations |
| `random_seed` | Re-run pin (copied onto the Harbor sidecar) |
| `kind` | `baseline`, `ab`, `cohort`, `model`, `prompt`, `policy`, `latency`, `accessibility` (metadata) |
| `task_path` / `model_name` / `agent_name` | Existing Harbor task + agent names |
| `metrics` | Declared metric ids (evaluation later) |
| `execution_budget` | `max_tokens`, `max_cost`, `max_duration_seconds`, `max_concurrency` |
| `governance` | `retention_days`, `requires_human_validation` (default true), `sign_off`, `notes` |
| `sample_size` / `n_attempts` | Trial shape for estimates and mapping |

`map_experiment_to_harbor_job` emits a Harbor job **document** (`job_name`, `agents`, `tasks`, `environment`) plus sidecar metadata (`experiment_id`, `tenant_id`, `seed`, policy). Same seed → same sidecar. `estimate_experiment_cost` is pre-run only.

### Policy

`PolicyDecision`: `ALLOW`, `DENY`, `ALLOW_WITH_REDACTION`, `ALLOW_WITH_APPROVAL`, `SANDBOX_ONLY`.  
`PolicyRequest` carries tenant, action, resource, optional persona/provider/classification.  
`default_simulation_decision` always returns `SANDBOX_ONLY`.

## Persistence

`EnterpriseRepository` is the contract. Two backends:

| Backend | When |
|---------|------|
| `InMemoryEnterpriseStore` | Default (`MATRIX_ENTERPRISE_STORE=memory`). Tests, ephemeral `enterprise-api`. |
| `SqliteEnterpriseStore` | `MATRIX_ENTERPRISE_STORE=sqlite`. Stdlib `sqlite3` + versioned migrations. Default file `.enterprise/store.sqlite`. |

Neither backend puts business rules in an ORM. Harbor’s Supabase client remains a **registry**, not this product store. Tenant slugs are unique.

## Backwards compatibility

- Existing `matraix run`, `matraix smoke`, Playground, and Harbor agents are unchanged.
- Existing persona YAML does not require `enterprise:` or `tenant_id`.
- Enterprise types are additive (`matraix.enterprise`).
