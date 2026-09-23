# ADR 0002: Persistence strategy and the generic record store

- **Status:** Accepted
- **Date:** 2026-09-23
- **Supersedes:** nothing. Extends [ADR 0001](0001-enterprise-platform-boundaries.md).

## Context

Phase 0–2 persisted nine entity types as hand-written SQL, one `put_`/`get_` pair and one table
per type. The build adds ~25 more entity types (identities, population versions, experiments,
runs, trials, metrics, policies, budgets, approvals, reports). Repeating that pattern would mean
~25 more tables and ~50 more hand-written statements per dialect, and every new field would be a
migration.

PostgreSQL is required for production; SQLite must remain the local/test backend; the in-memory
store must remain the default so existing tests keep running with no setup.

## Decision

1. **One generic, versioned record table** (`enterprise_records`) keyed by
   `(tenant_id, kind, id)` with indexed `organization_id`, `parent_id`, `status`, `created_at`
   and a JSON `body`. New entity types need **no** schema change.
2. **A shared envelope.** Every new entity subclasses `EnterpriseRecord` and carries `id`,
   `tenant_id`, `organization_id`, `created_at`, `updated_at`, `created_by`, `version`, `status`,
   `metadata`, `provenance`. Records are frozen; mutation is `with_update(...)`, which bumps
   `version`.
3. **Optimistic concurrency in the store.** `put_record` requires the new version to be exactly
   `stored.version + 1` (or an explicit `expected_version`) and raises `ConcurrencyError`
   otherwise, so a lost update cannot pass silently.
4. **Dedicated tables where access patterns differ**: `audit_events` (append-only, hash-chained),
   `work_items` (leased queue with `SKIP LOCKED` on PostgreSQL), `persona_snapshots` (high row
   count, read by range), `idempotency_keys`.
5. **One dialect-parametrized base.** `SqlRecordStoreBase` writes `?`-placeholder SQL rewritten
   for `%s` dialects; `SqliteEnterpriseStore` and `PostgresEnterpriseStore` inherit it.
6. **The Phase 0–2 typed tables stay.** Migrations 1–2 are never edited; migration 3 adds the
   generic tables. The legacy entities keep their existing typed SQL on SQLite and are stored as
   JSON documents on PostgreSQL through the same codec.
7. **Explicit transaction boundaries.** `store.transaction()` wraps compound operations
   (materialize writes snapshots and flips version status together) and rolls back on error in
   all three backends.

## Consequences

### Positive

- Adding an entity costs a model class, not a migration.
- Isolation and concurrency rules live in one place instead of being re-implemented per entity.
- The same test suite runs against in-memory, SQLite and PostgreSQL.

### Negative / follow-ups

- Queries on JSON fields (`RecordQuery.where`) filter in Python after an indexed narrowing.
  Frequently filtered fields should be promoted to real columns when a hot path appears; the
  PostgreSQL path can later use `jsonb` operators.
- Two persistence shapes coexist (typed legacy tables, generic records). Collapsing the legacy
  nine into the generic table is possible later but was not worth a breaking migration now.
