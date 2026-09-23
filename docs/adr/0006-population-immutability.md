# ADR 0006: Population versions are immutable and deterministic

- **Status:** Accepted
- **Date:** 2026-09-23

## Context

A result is only auditable if the population it ran against can be reconstructed exactly. The
existing generation pipelines (Full-DAG synthesis, the 1M coreset, evidence-grounded extraction)
are sound but produce ad-hoc pools with no identity, no immutability and no recorded methodology.

## Decision

1. **Materialization produces a versioned snapshot**, never a mutable "current" population.
   Re-materializing creates version *n+1*; existing versions are never rewritten.
2. **Determinism by construction.** Per-segment seeds derive only from the caller's seed, the
   segment index and the segment name — never from wall-clock time or a random id — so the same
   declaration and seed produce byte-identical persona files and the same `manifest_hash`.
3. **Content addressing.** Every persona record is hashed; the version carries a `manifest_hash`
   over the ordered list of those hashes. Cohorts hash their selection plus the chosen personas.
4. **Persona YAML on disk stays the contract.** Materialization writes the same record shape
   Harbor's `persona_path` already consumes, plus a `manifest.json` readable by existing MatrAIx
   tooling. The database stores manifests, hashes, weights and quality — not 1,290 columns.
5. **Methodology is recorded, not implied:** source datasets, schema version, sampling method,
   seed, filters, constraints, weights, generator version, model version (null when no model was
   involved), privacy mode, validation findings.
6. **Representativeness is never inferred.** The version carries an explicit
   `representativeness_claim`, defaulting to a statement that this is a simulated population not
   validated against a real human population.
7. **Failures are distinguished.** Planning failures (a segment's pool cannot meet its quota)
   raise before any version exists, so a rejected request leaves no orphan and consumes no version
   number. Failures while writing mark the created version `FAILED` with the reason attached.
8. **Quality is measured, and severity is honest.** Cross-dimension contradictions are reported as
   a *warning* carrying the measured corpus baseline (32% of the repository's own dev-sample
   personas, 43% of freshly Full-DAG-sampled ones already fail the same validator), because
   treating a normal property of the upstream data as an error would mark every population failed.
   Constraint violations and empty populations are errors.

## Consequences

- An experiment can pin `population_version_id` and be re-run against exactly the same people.
- Oversampling with replacement is opt-in and recorded in provenance, so a quietly padded
  population is not possible.
- Very large populations write many files; sharded/object storage is a deployment follow-up.
