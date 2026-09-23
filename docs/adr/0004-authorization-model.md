# ADR 0004: Authentication providers and the RBAC model

- **Status:** Accepted
- **Date:** 2026-09-23

## Context

The research Playground has no authentication, and Phase 1 shipped a single shared bearer token
with no notion of who is calling. Enterprise deployment needs real principals, least-privilege
roles, service accounts and federated identity — without making local development require an
identity provider.

## Decision

1. **A provider chain, first hit wins:** static local tokens → service accounts (hashed tokens in
   the store) → OIDC/JWT. Each provider returns a `Principal` or `None`.
2. **`MATRIX_ENTERPRISE_API_TOKEN` keeps working** and is interpreted as a *platform admin*
   credential, preserving the Phase 1 contract.
3. **Dev-open default, strict opt-in.** With no credential configured the API stays open and logs
   one warning, matching the historic local behaviour. `MATRIX_ENTERPRISE_AUTH_MODE=strict`
   refuses to start unauthenticated, for shared deployments.
4. **Five roles, strictly nested:** viewer ⊂ analyst ⊂ researcher ⊂ operator ⊂ admin.
   Permissions are `resource.verb` strings. A researcher may launch experiments but not approve
   them; an operator may approve and read audit but not manage identities.
5. **Authorization is a function of (principal, permission, tenant).** `guard(...)` checks both
   permission and tenant scope, and writes a denial audit row before raising 403.
6. **Raw credentials are never stored.** Service-account tokens are persisted as peppered SHA-256
   digests plus a short display prefix; the raw token is returned exactly once at creation.
7. **JWT verification is real verification:** signature, issuer, audience and expiry are all
   checked, with JWKS by value or by URL.

## Consequences

- Local `matraix enterprise-api` still works with no configuration.
- Role changes are one edit to a permission matrix, covered by a test asserting the nesting.
- Approval-gated actions have a distinct permission, so Phase 3 can require a second principal.
- SCIM provisioning and per-workspace scoping are not implemented; `RoleBinding` carries a scope
  field ready for that.
