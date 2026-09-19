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

Named nodes for later org-graph edges. Team may point at a department. Cross-tenant foreign keys are rejected at construction.

### Population

Named, tenant-owned set with `organization_id`, optional `team_id`, optional `target_size`. This does not yet generate YAML pools; it is the control-plane handle Playground datasets will attach to.

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

### Experiment (skeleton)

Control-plane record: `hypothesis`, `objective`, `population_ids`, `random_seed`, `data_classification`, `default_policy` (default `SANDBOX_ONLY`), `execution_budget`, `variables`.

Does **not** launch Harbor jobs in Phase 0.

### Policy

`PolicyDecision`: `ALLOW`, `DENY`, `ALLOW_WITH_REDACTION`, `ALLOW_WITH_APPROVAL`, `SANDBOX_ONLY`.  
`PolicyRequest` carries tenant, action, resource, optional persona/provider/classification.  
`default_simulation_decision` always returns `SANDBOX_ONLY`.

## Persistence

Phase 0: `InMemoryEnterpriseStore` — dicts partitioned by `tenant_id`. No SQL, no Harbor DB reuse (Harbor’s Supabase client is a **registry**, not a product store).

Phase 1 will add repository interfaces backed by migrations without putting business rules in ORM models.

## Backwards compatibility

- Existing `matraix run`, `matraix smoke`, Playground, and Harbor agents are unchanged.
- Existing persona YAML does not require `enterprise:` or `tenant_id`.
- Enterprise types are additive (`matraix.enterprise`).
