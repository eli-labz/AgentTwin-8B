# ADR 0005: Append-only, hash-chained audit

- **Status:** Accepted
- **Date:** 2026-09-23

## Context

The brief requires an append-only security audit stream covering authentication, resource
creation and mutation, experiment launch, policy decisions, approvals, denials, secret access
attempts, artifact access and administrative actions — separable from job telemetry, and
trustworthy enough to answer "who did this".

## Decision

1. **No update or delete surface.** The store exposes `append_audit`, `last_audit`, `list_audit`.
   There is no API, route or store method that edits or removes an audit row.
2. **Per-tenant hash chain.** Each row carries a monotonic `seq`, the previous row's hash, and its
   own hash over its content plus that previous hash. `verify_chain()` recomputes oldest→newest and
   reports the first break, so editing or deleting a row is detectable even by someone with
   database access.
3. **Denials are audited, not just successes**, and a failed privileged operation is audited with
   `outcome=failure` — an attempt that went wrong must leave a trail.
4. **Details are redacted on write.** Known secret values and common credential shapes are
   replaced before the row is stored, so the audit trail cannot become a secret store.
5. **Separate from job artifacts.** Harbor's per-trial output stays where it is; the audit stream
   is a control-plane concern with its own table and its own read permission.

## Consequences

- Tampering is detectable but not prevented; append-only storage (WORM, external log shipping) is
  a deployment concern documented rather than implemented.
- The chain is per tenant, so one tenant's verification is independent of another's.
- `verify_chain` reads the whole stream; for very long streams it will need a checkpointing scheme.
