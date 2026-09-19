# Enterprise API (`/api/v1`)

Phase 1 control-plane skeleton. This is **not** the Playground API
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
