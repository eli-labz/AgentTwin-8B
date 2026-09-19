# Enterprise API (`/api/v1`)

Phase 1–4 control-plane skeleton. This is **not** the Playground API
(`docs/application/playground-api.md`). Harbor jobs and `matraix run` stay
unchanged.

Interactive OpenAPI: `/docs` and `/openapi.json` (FastAPI).

## Run

```bash
# In-memory (default, process-local)
uv run matraix enterprise-api --port 8090

# Durable SQLite (path is local; gitignored default .enterprise/store.sqlite)
export MATRIX_ENTERPRISE_STORE=sqlite
export MATRIX_ENTERPRISE_DB=.enterprise/store.sqlite
# Optional: require Authorization: Bearer …
export MATRIX_ENTERPRISE_API_TOKEN=  # set in the environment; never commit
uv run matraix enterprise-api --port 8090
```

Equivalent: `uvicorn matraix.enterprise.api:app --port 8090`.

## Conventions

- JSON field names match the domain model (snake_case).
- Tenant-scoped routes require `X-Tenant-Id`.
- Cross-tenant access is `403`. Missing records inside the tenant are `404`.
- Invalid schema is `400`.
- When `MATRIX_ENTERPRISE_API_TOKEN` is unset, the API is open (local/dev).
  When set, every `/api/v1` call needs `Authorization: Bearer <token>`.
  `/docs` and `/openapi.json` stay reachable so operators can read the contract.
- Creating a tenant also creates a default organization (same name) so
  populations and personas can be created immediately.

## Endpoints

| Method | Path | Tenant header | Purpose |
|--------|------|---------------|---------|
| `POST` | `/api/v1/tenants` | no | Create tenant + default organization |
| `GET` | `/api/v1/tenants` | no | List tenants |
| `GET` | `/api/v1/tenants/{tenant_id}` | optional; must match if sent | Tenant detail + orgs |
| `GET` | `/api/v1/organizations` | required | Orgs for `X-Tenant-Id` |
| `POST` | `/api/v1/populations` | required | Create population (default org if omitted) |
| `GET` | `/api/v1/populations` | required | List populations for the tenant |
| `GET` | `/api/v1/populations/{id}` | required | Get one population |
| `POST` | `/api/v1/personas` | required | Wrap an existing YAML `record` |
| `GET` | `/api/v1/personas` | required | List; optional `?population_id=` |
| `GET` | `/api/v1/personas/{id}` | required | Get one persona |
| `POST` | `/api/v1/org-edges` | required | Create a directed org-graph edge |
| `GET` | `/api/v1/org-edges` | required | List edges; optional `?relation=` |
| `GET` | `/api/v1/org-edges/{id}` | required | Get one edge |
| `DELETE` | `/api/v1/org-edges/{id}` | required | Delete one edge (`204`) |
| `POST` | `/api/v1/population-declarations` | required | Create population + validate shape |
| `PUT` | `/api/v1/populations/{id}/declaration` | required | Attach/replace a shape on a population |
| `GET` | `/api/v1/populations/{id}/declaration` | required | Read the validated shape |
| `POST` | `/api/v1/experiments` | required | Create a launch record (default `SANDBOX_ONLY`) |
| `GET` | `/api/v1/experiments` | required | List experiments for the tenant |
| `GET` | `/api/v1/experiments/{id}` | required | Get one experiment |
| `POST` | `/api/v1/experiments/{id}/estimate` | required | Pre-run cost estimate (does not launch) |
| `GET` | `/api/v1/experiments/{id}/harbor-job` | required | Mapped Harbor job document + sidecar |
| `POST` | `/api/v1/policy/evaluate` | required | Policy gateway (`SANDBOX_ONLY` default) |
| `GET` | `/api/v1/model-policy` | required | Tenant model policy (sandbox default if unset) |
| `PUT` | `/api/v1/model-policy` | required | Replace tenant allow-list / residency / flags |
| `GET` | `/api/v1/models/catalog` | required | Named providers + `allowed` under tenant policy |
| `POST` | `/api/v1/models/route` | required | Capability / residency / cost routing |
| `POST` | `/api/v1/models/complete` | required | Policy-gated completion (sandbox mock / dry-run) |

### Create tenant

```json
{ "name": "Acme Research", "slug": "acme" }
```

### Create population

```json
{ "name": "Workforce", "target_size": 10000, "organization_id": null }
```

### Create persona (legacy YAML mapping)

```json
{
  "record": {
    "persona_id": "0042",
    "version": "1.0",
    "source": "synthetic",
    "dimensions": { "age_bracket": "25-34", "role_function": "Teaching" }
  },
  "population_id": "pop_…"
}
```

`record` is the existing Playground/Harbor persona document. Enterprise
dimensions remain optional. Synthetic personas are simulation parameters —
not psychological equivalents of humans.

### Org-graph edge

```json
{
  "relation": "reports_to",
  "source_kind": "persona",
  "source_id": "per_…",
  "target_kind": "persona",
  "target_id": "per_…"
}
```

Relations: `reports_to`, `member_of`, `collaborates_with`, `depends_on`,
`approves`, `escalates_to`, `serves`, `supplies`, `reviews`, `owns_process`,
`owns_system`. Labeled `process` / `system` targets do not require a stored entity.

### 10k-shaped population declaration

```json
{
  "name": "10k workforce",
  "target_size": 10000,
  "backend": "coreset_1m",
  "include_org_structure": true,
  "segments": [
    { "name": "engineering", "share": 0.4 },
    { "name": "support", "share": 0.35 },
    { "name": "other", "share": 0.25 }
  ]
}
```

`backend` is one of `treiver`, `full_dag`, `coreset_1m`. The API validates
and stores the shape (`resolved_counts` sum to `target_size`). It does not
run `persona/synthesis`. Privacy default: `aggregate_stats_then_synthetic`.

### Experiment launch record

```json
{
  "hypothesis": "Novice users retry more often",
  "objective": "Measure retry rate",
  "random_seed": 42,
  "kind": "ab",
  "task_path": "application/tasks/example-survey_product-feedback",
  "sample_size": 4,
  "metrics": ["retry_rate"],
  "execution_budget": { "max_cost": 5.0, "max_concurrency": 2 },
  "governance": { "retention_days": 14, "sign_off": "lead" }
}
```

`POST .../estimate` returns trial count, estimated tokens/USD, and
`SANDBOX_ONLY` or `DENY_BUDGET`. `GET .../harbor-job` returns `{harbor_job, sidecar}`
— a YAML-shaped document, not a `harbor.Job` instance. `matraix run` defaults
are unchanged. Rates: `MATRIX_ENTERPRISE_USD_PER_1K_TOKENS` (default 0.003)
and `MATRIX_ENTERPRISE_TOKENS_PER_TRIAL` (default 4000).

### Policy evaluate

```json
{
  "action": "complete",
  "resource": "model.complete",
  "model_provider": "anthropic",
  "data_classification": "RESTRICTED",
  "destination": "external"
}
```

Returns `{ decision, reasons, redaction_required, approval_required }`. Default
without overrides is `SANDBOX_ONLY`. Forbidden providers and `RESTRICTED` +
external are `DENY`. `DENY` on `/models/complete` is HTTP `403`.

### Model policy and routing

`PUT /api/v1/model-policy` stores allow/deny lists, residency, cost/latency
caps, `allow_external`, and `allow_live`. Env overlays:
`MATRIX_ENTERPRISE_MODEL_ALLOWLIST`, `MATRIX_ENTERPRISE_MODEL_DENYLIST`,
`MATRIX_ENTERPRISE_MODEL_RESIDENCY`, `MATRIX_ENTERPRISE_ALLOW_EXTERNAL`,
`MATRIX_ENTERPRISE_ALLOW_LIVE`. No secrets are stored. See
[model-gateway.md](model-gateway.md).

## Persistence

| `MATRIX_ENTERPRISE_STORE` | Backend |
|---------------------------|---------|
| `memory` (default) | `InMemoryEnterpriseStore` — tests and ephemeral API |
| `sqlite` | `SqliteEnterpriseStore` — stdlib `sqlite3`, migrations in `matraix.enterprise.migrations` |

Repository interface: `EnterpriseRepository`. Business rules stay on entities;
the SQL layer only stores and reconstructs them.

## Job sidecar metadata

`application/scripts/generate_application_job.py --tenant-id <id>` writes
`tenant_id` onto the generated `.meta.json` only. `matraix run` defaults are
unchanged; Harbor job YAML is not tenant-prefixed in this phase.
