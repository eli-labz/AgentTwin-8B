# Threat model — AgentTwin Enterprise

Scope: the enterprise control plane (`matraix.enterprise`) and its API. The Harbor trial runtime
and the research Playground keep their existing posture and are called out where they matter.

## Assets

| Asset | Why it matters |
|-------|----------------|
| Tenant data (populations, experiments, runs, metrics, reports) | Commercial confidentiality between tenants |
| Provider credentials (model API keys, database URLs, service tokens) | Direct financial and access impact |
| Audit trail | Evidence of who did what; target for tampering |
| Persona records | Synthetic, but organizational modelling can encode business structure |
| Compute budget | Uncontrolled execution is a financial denial-of-service |

## Trust boundaries

1. **Caller → API.** Unauthenticated by default in local development; authenticated and
   role-checked once any credential is configured. Strict mode refuses to serve unauthenticated.
2. **Tenant → tenant.** Enforced in the store, not the handler (see ADR 0003).
3. **Control plane → execution.** Trials run in Harbor environments with their own network policy.
4. **Platform → model providers.** Outbound calls carry credentials resolved from the secret
   provider; the default execution posture is sandbox-only.

## Threats and mitigations

| Threat | Mitigation | Status |
|--------|-----------|--------|
| Cross-tenant read via guessed id | Tenant-scoped storage; foreign records are 404 | Implemented, tested |
| Cross-tenant widening via `X-Tenant-Id` | Header may only widen for platform admins; mismatch is 403 | Implemented, tested |
| Cross-tenant reference (persona → other tenant's population) | Referenced entities resolved tenant-scoped | Implemented, tested |
| Privilege escalation by a low-privilege role | Nested permission matrix; per-route permission checks | Implemented, tested |
| Stolen service-account token | Only hashed tokens stored; revocable; expiry supported | Implemented, tested |
| Forged or replayed JWT | Signature, issuer, audience, expiry verified | Implemented, tested |
| Secret leaking into logs, audit, artifacts | Secret-provider indirection, redaction filter, audit redaction | Implemented, tested |
| Audit tampering | Per-tenant hash chain; no update/delete surface; `verify_chain` | Detection implemented |
| Lost update / concurrent mutation | Optimistic concurrency with `ConcurrencyError` | Implemented, tested |
| Duplicate side effects from retries | Idempotency keys; work items terminal once completed | Implemented, tested |
| Runaway spend | Budget and quota entities exist; enforcement at launch | **Planned (Phase 3)** |
| Unreviewed external side effects | Default policy posture is sandbox-only; approvals modelled | **Enforcement planned (Phase 3)** |
| Artifact access across tenants on disk | Harbor `jobs/` tree is not tenant-partitioned | **Open** |
| Denial of service via huge populations | Quota caps modelled; materialization is synchronous | **Partially open** |

## Explicit non-goals

- The control plane does not attempt to prevent a tenant administrator from misusing their own
  tenant's data.
- Simulated-user output is not a security control and is not evidence about real people.
- Append-only *storage* (WORM media, external log shipping) is a deployment responsibility; the
  application provides detection, not prevention.

## Reporting

Security contact and disclosure process: see [SECURITY.md](../../SECURITY.md).
