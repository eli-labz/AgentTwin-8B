# ADR 0003: Where tenant isolation is enforced

- **Status:** Accepted
- **Date:** 2026-09-23

## Context

Tenant isolation implemented at the HTTP edge fails the moment a code path bypasses the edge:
a CLI command, a background worker, a scheduled job, or a new route whose author forgot the
filter. The brief requires enforcement in the repository/service layer, not only in handlers.

## Decision

1. **Tenancy is part of identity.** Scoped identifiers embed their `TenantId`
   (`PersonaId(tenant, value)`), so a foreign id cannot be used as a lookup key without failing
   a check. Entity constructors reject cross-tenant references at construction time.
2. **Every store operation takes a tenant.** There is no "get by id" without a tenant argument.
   Rows are partitioned by `(tenant_id, …)`.
3. **A foreign tenant's record is indistinguishable from a missing one.** Generic record reads
   raise `EntityNotFoundError` (404), not a permission error, so probing cannot confirm whether
   an id exists in another tenant. The legacy scoped-id path keeps raising
   `CrossTenantAccessError` (403) for a structurally foreign id, because there the id itself
   already proves the caller constructed it for another tenant.
4. **The request's tenant comes from the principal, not from a header.** `X-Tenant-Id` may only
   *widen* scope for a platform admin. For a tenant-bound principal a mismatched header is 403,
   resolved before any handler body runs.
5. **Cross-tenant references are impossible, not merely unauthorized.** Creating a persona that
   points at another tenant's population fails because the population lookup is tenant-scoped.

## Consequences

- A new route inherits isolation by calling the store; forgetting a filter is not possible
  because there is no unfiltered call.
- Tests assert the invariant at the store layer *and* over HTTP, for in-memory and SQLite
  (`tests/multitenancy/`), including the work queue, audit stream, persona snapshots and
  idempotency keys.
- Harbor `jobs/<job>/` directories on disk remain outside this boundary; artifact-level tenant
  separation is tracked as remaining work.
