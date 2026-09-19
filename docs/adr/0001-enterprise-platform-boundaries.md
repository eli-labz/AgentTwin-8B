# ADR 0001: Enterprise platform boundaries

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** AgentTwin Enterprise Phase 0

## Context

AgentTwin-8B (this repository) is a working research platform: 1,290-dimension personas, Harbor trial runtime, Playground, and four evaluation environments. The enterprise program needs tenancy, policy, org twins, and a control plane.

Rewriting Harbor or replacing the persona schema would destroy the strengths the product must keep.

## Decision

1. **Extend, do not replace.** Harbor `Job`/`Trial`, Playground, `persona/schema/dimensions.json`, and `matraix` CLI remain the simulation path.
2. **New package `matraix.enterprise`** holds cloud-neutral domain types (IDs, tenant hierarchy, optional enterprise persona fields, experiment skeleton, policy enums) and tenant-partitioned repositories.
3. **Tenancy is a property of identity.** Scoped IDs embed `TenantId`. Stores keyed by tenant raise `CrossTenantAccessError` on mismatch instead of returning foreign data.
4. **Existing persona YAML stays valid.** Enterprise dimensions are an optional `enterprise:` block. Legacy `persona_id` is retained as `legacy_persona_id`.
5. **Provider independence.** Core entities have no Modal/GCP/AWS fields. Existing compute-family adapters stay in Playground/Harbor.
6. **Simulation-only default.** `PolicyDecision.SANDBOX_ONLY` is the default enterprise execution posture. Synthetic personas are simulation parameters, not psychological equivalents of humans.
7. **Phased delivery.** Phase 0 is docs + domain + tests. APIs, persistence, and console come in later phases so the repo stays runnable each step.

## Consequences

### Positive

- Incremental adoption; existing tests and smoke jobs keep working.
- Cross-tenant bugs fail at the type/store boundary before HTTP exists.
- Clear seam for a future `/api/v1` and SQL repositories.

### Negative / follow-ups

- Dual identifiers (`legacy_persona_id` vs `PersonaId`) until Playground learns tenants.
- Harbor `jobs/` directories are not tenant-isolated until Phase 1 metadata wrapping.
- In-memory store is not durable; must not be mistaken for production IAM.

## Alternatives rejected

- **New top-level monorepo package** — extra packaging cost; `matraix` is already the product install.
- **Put `tenant_id` only on Harbor job YAML** — easy to forget on personas, logs, and caches; isolation would be conventional, not structural.
- **Replace Harbor with a new orchestrator in Phase 0** — violates “repo stays runnable” and throws away trial/verifier/environment work.

## References

- [REPOSITORY_AUDIT.md](../enterprise/REPOSITORY_AUDIT.md)
- [TARGET_ARCHITECTURE.md](../enterprise/TARGET_ARCHITECTURE.md)
- [DOMAIN_MODEL.md](../enterprise/DOMAIN_MODEL.md)
