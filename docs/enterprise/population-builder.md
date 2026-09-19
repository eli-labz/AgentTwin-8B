# Population builder

Phase 2 control-plane declaration for a shaped workforce (including 10k).
This module **composes** existing generation backends; it does not replace
Treiver (`persona/extraction`), Full-DAG sampling (`persona/synthesis`), or
the public 1M coreset.

## What it does

`matraix.enterprise.population_builder.build_population_declaration`:

1. Validates `target_size`, backend name, and segments (`count` or `share`).
2. Resolves integer counts that **sum to** `target_size` (share remainder
   lands on the last segment; a positive share that rounds to 0 fails).
3. Checks catalog filter values when the dimension is in `dimensions.json`.
4. Rejects privacy modes / constraints that clone identifiable employees.
5. Persists the declaration beside a `Population` (in-memory or SQLite).

It does **not** write persona YAML, call an LLM, or import synthesis scripts.

## Backends (names only)

| `backend` | Existing pipeline |
|-----------|-------------------|
| `treiver` | `persona/extraction` |
| `full_dag` | `persona/synthesis` Full-DAG sampler |
| `coreset_1m` | `persona/datasets/matraix-persona-1m` |

A later phase can fill the declaration by calling those tools. Phase 2 stops
at a validated, tenant-scoped spec.

## Privacy

Default `privacy_mode` is `aggregate_stats_then_synthetic`: derive constraints
from aggregate stats, then synthesize. Modes such as `clone_identifiable_employees`
fail closed.

## API

`POST /api/v1/population-declarations` — see [api.md](api.md).
