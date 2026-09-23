# Enterprise API (`/api/v1`)

Phase 1–2 control-plane skeleton. This is **not** the Playground API
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

## Authentication and authorization

Providers are tried in order: static local tokens, service accounts (hashed tokens in the store),
then OIDC/JWT. Configure any of:

```bash
# Platform-admin shared token (backward compatible)
export MATRIX_ENTERPRISE_API_TOKEN=...

# Per-principal static tokens: token -> {subject, tenant_id, roles, kind, platform_admin}
export MATRIX_ENTERPRISE_AUTH_TOKENS='{"tok":{"subject":"alice","tenant_id":"tnt_x","roles":["researcher"]}}'

# OIDC / JWT
export MATRIX_ENTERPRISE_OIDC_ISSUER=https://idp.example
export MATRIX_ENTERPRISE_OIDC_AUDIENCE=agenttwin
export MATRIX_ENTERPRISE_OIDC_JWKS_URL=https://idp.example/.well-known/jwks.json
# optional: _TENANT_CLAIM (default tenant_id), _ROLES_CLAIM (default roles)

# Refuse to serve unauthenticated (recommended for any shared deployment)
export MATRIX_ENTERPRISE_AUTH_MODE=strict
```

With **no** credential configured the API stays open for local development and logs one warning.

Roles are nested: `viewer ⊂ analyst ⊂ researcher ⊂ operator ⊂ admin`. Permissions are
`resource.verb` strings; `GET /api/v1/roles` returns the full matrix. A researcher can launch
work, an operator can approve it and read audit, an admin manages identities.

The tenant a request acts in comes from the **principal**. `X-Tenant-Id` may only widen scope for
a platform admin; for a tenant-bound principal a mismatched header is `403`.

## Errors, request ids and idempotency

Every response carries `X-Request-Id` (honoured from the request when supplied). Errors use a
stable object:

```json
{"detail": "…", "error": {"code": "not_found", "message": "…", "request_id": "req_…"}}
```

Codes: `invalid_request` (400), `unauthenticated` (401), `forbidden` / `cross_tenant_access` (403),
`not_found` (404), `conflict` (409), `validation_error` (422).

Mutating launch-style operations accept an `Idempotency-Key` header. Replaying the same key
returns the first response instead of performing the work twice.

List endpoints return a bare JSON array by default (unchanged). Passing `?limit=` switches to an
envelope: `{"items": [...], "total": N, "limit": L, "offset": O, "next_offset": N|null}`.

## Health

`GET /health` is liveness. `GET /ready` reports readiness, the active store class and whether
authentication is open; it returns `503` when the store is unreachable.

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
| `GET` | `/api/v1/whoami` | no | Authenticated principal, roles and permissions |
| `GET` | `/api/v1/roles` | no | Role → permission matrix |
| `POST` | `/api/v1/users` | required | Create a tenant user (`identity.write`) |
| `GET` | `/api/v1/users` | required | List users (`identity.read`) |
| `POST` | `/api/v1/service-accounts` | required | Create a service account; returns the token **once** |
| `GET` | `/api/v1/service-accounts` | required | List service accounts (hash and raw token never returned) |
| `POST` | `/api/v1/service-accounts/{id}/disable` | required | Revoke a service account |
| `POST` | `/api/v1/role-bindings` | required | Bind a role to a principal |
| `GET` | `/api/v1/role-bindings` | required | List role bindings |
| `POST` | `/api/v1/organizations` | required | Create an organization (`organization.write`) |
| `POST` | `/api/v1/populations/{id}/versions` | required | **Materialize** an immutable population version |
| `GET` | `/api/v1/populations/{id}/versions` | required | List versions of a population |
| `GET` | `/api/v1/population-versions/{id}` | required | Version detail with full provenance |
| `GET` | `/api/v1/population-versions/{id}/quality` | required | Quality report and validity classification |
| `GET` | `/api/v1/population-versions/{id}/composition` | required | Aggregate subgroup composition (no full records) |
| `GET` | `/api/v1/population-versions/{id}/personas` | required | Paginated persona snapshot manifest |
| `POST` | `/api/v1/population-versions/{id}/freeze` | required | Mark a ready version frozen |
| `POST` | `/api/v1/population-versions/{id}/cohorts` | required | Create a reproducible cohort |
| `GET` | `/api/v1/cohorts` | required | List cohorts; optional `?population_version_id=` |
| `GET` | `/api/v1/cohorts/{id}` | required | Cohort detail |
| `GET` | `/api/v1/audit-events` | required | Read the append-only audit stream (`audit.read`) |
| `GET` | `/api/v1/audit-events/verify` | required | Verify the tenant's audit hash chain |

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
