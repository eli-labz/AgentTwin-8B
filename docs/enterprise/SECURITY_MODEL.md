# Security Model — AgentTwin Enterprise

## Current baseline (inspected)

See [REPOSITORY_AUDIT.md](REPOSITORY_AUDIT.md) §17. Summary:

- Playground HTTP API is **unauthenticated** in local/dev (`docs/application/playground-api.md`).
- Model keys come from **environment variables**; `matraix.provider_credentials` never prints secret values.
- Harbor Viewer / registry use **GitHub OAuth via Supabase**.
- Tasks may restrict egress (`NetworkMode`: no-network / public / allowlist).
- Host survey/chat can honor `MATRIX_MAX_COST_USD` (`playground.budget.BudgetExceededError`).

Phase 0 does not turn Playground into an IdP. It defines the **domain isolation rules** later APIs must implement.

## Tenancy isolation

**Invariant:** no cross-tenant access by default.

Enforced in Phase 0 **by construction**:

1. Every non-tenant id embeds a `TenantId`.
2. Entity constructors reject foreign-key IDs from another tenant (`EnterpriseSchemaError`).
3. Store buckets are keyed by tenant. `get_*` requires `(tenant_id, scoped_id)`.
4. If `scoped_id.tenant_id != tenant_id`, the store raises `CrossTenantAccessError` — it does not search other buckets and does not return a silent miss that could be used for existence oracles across tenants.

Future bindings (Phase 1+), each tenant-scoped:

- REST queries, object storage prefixes, logs, analytics, vector indexes, caches, job queues, artifacts, secrets, reports.

Legacy Harbor `jobs/<job_name>/` is **not** tenant-prefixed today. Wrapping jobs with `tenant_id` in metadata is a Phase 1 task; do not claim filesystem isolation exists yet.

## Policy

Every future execution must evaluate a `PolicyRequest` and receive a `PolicyDecision`:

| Decision | Meaning |
|----------|---------|
| `ALLOW` | Proceed (still subject to network policy) |
| `DENY` | Block |
| `ALLOW_WITH_REDACTION` | Proceed after stripping classified fields |
| `ALLOW_WITH_APPROVAL` | Hold for a human gate |
| `SANDBOX_ONLY` | Simulation / mock integrations only |

**Default for enterprise tests:** `SANDBOX_ONLY` (`default_simulation_decision`). Live production actions require explicit policy and authorization (not implemented in Phase 0).

Policy inputs (contract): tenant, persona, task, environment, tool, data classification, model provider, action, resource.

## Data classification

Labels on enterprise records (`DataClassification`):

- `PUBLIC`
- `INTERNAL` (default)
- `CONFIDENTIAL`
- `RESTRICTED`

Intended restrictions (Phase 1+ policy rules): external model access, persistence, export, logging, analytics, human visibility.

**Never** put secrets in model prompts or logs. Today’s preflight already avoids printing keys; classification must extend that to persona text and artifacts.

## Secrets

| Rule | Status |
|------|--------|
| No hard-coded production secrets | Observed (tests inject fakes) |
| Secrets abstraction (vault / workload identity) | Not implemented |
| Encryption-ready persistence | Domain model has no storage format that precludes it |
| Provider independence | Credential **names** are env vars; core entities have no cloud fields |

## Identity (target, not Phase 0)

Authentication: OIDC, OAuth 2.x, SAML-compatible SSO, SCIM-compatible provisioning, service accounts, API tokens, workload identity.

Authorization: RBAC + optional ABAC. Suggested roles: PlatformAdmin, TenantAdmin, SimulationAdmin, Researcher, Evaluator, Developer, Auditor, Viewer. Every permission explicit.

Playground must not remain an open CORS API once it binds to this model.

## Secure defaults (checklist)

From the master prompt — status after Phase 0:

| Requirement | Phase 0 |
|-------------|---------|
| Tenant isolation in domain/store | Yes (in-memory) |
| Policy vocabulary + sandbox default | Yes |
| Classification enum | Yes |
| No hard-coded secrets in new code | Yes |
| API authorization / CSRF / rate limits | Not yet |
| Audit log | Not yet |
| Dependency scanning in CI | Not yet |
| Container isolation | Existing Harbor/Docker (unchanged) |

See also root [SECURITY.md](../../SECURITY.md).
