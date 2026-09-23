# Build Report — AgentTwin Enterprise

**Date:** 2026-09-23 · **Repository:** `eli-labz/AgentTwin-8B` (`main`, from `488a296`)

## Executive summary

This build delivered **Phases 0–2** of the enterprise brief — repository baseline, production
persistence with tenant isolation, identity/RBAC/audit, and the population engine — as working,
tested code. **Phases 3–8 were not delivered**: the experiment lifecycle, distributed
orchestration, model gateway, hierarchical evaluation, enterprise console, Python SDK, simulation
primitives and deployment assets remain open. Sections below label every capability as
implemented-and-tested or planned; nothing is claimed on the strength of an interface alone.

The research platform is intact. Harbor, the Playground, the persona schema and the `matraix` CLI
were extended, never replaced: `matraix run | results | smoke | enterprise-api` keep their flags,
the repository test tree grew from 994 to 1,068 passing tests with no regressions, and the survey
smoke path still passes without Docker or an API key.

## Architecture implemented

Four logical layers, additive to the existing runtime:

- **Control layer** — tenants, organizations, users, service accounts, roles and bindings,
  population definitions and versions, cohorts, policies, budgets, quotas, approvals (entities and
  authorization implemented; budget/policy *enforcement* at launch is Phase 3).
- **Data layer** — a generic, tenant-partitioned, versioned record store with optimistic
  concurrency, plus dedicated tables for the append-only audit chain, the leased work queue,
  persona snapshots and idempotency keys.
- **Execution layer** — unchanged. Harbor remains the trial engine; the population engine writes
  persona YAML in exactly the shape `persona_path` already consumes.
- **Observability layer** — the audit stream, request ids and structured error objects are in
  place. OpenTelemetry tracing is Phase 4.

Key decisions are recorded as ADRs 0002–0006 (persistence, tenant enforcement, authorization,
audit, population immutability), each listing alternatives rejected.

## Files and modules added

| Area | Modules |
|------|---------|
| Domain | `enterprise/domain/base.py`, `enterprise/domain/models.py` (24 record types + vocabularies) |
| Persistence | `enterprise/records.py` (contract, in-memory, dialect-parametrized SQL base), `enterprise/postgres_store.py`, `enterprise/legacy_codec.py` |
| Identity | `enterprise/auth/{roles,principal,providers,tokens}.py` |
| Security | `enterprise/audit.py`, `enterprise/secrets.py` |
| Composition | `enterprise/context.py` |
| API | `enterprise/routes/{common,identity,audit,populations}.py`, rewritten `enterprise/api.py` |
| Population engine | `enterprise/population/{quality,materialize,cohorts}.py` |

Measured: 13 new Python modules and 9 new test files, ~7,800 lines added; the
`matraix.enterprise` package is now 36 modules / ~10,400 lines. Five new subpackages are
registered in `pyproject.toml` (the package list is explicit, so omission would break installs).

## Database migrations

Migrations 1–2 (Phase 0–2 typed tables) are untouched. **Migration 3** adds `enterprise_records`
(generic versioned records, indexed by tenant/kind/organization/parent/status/created_at),
`audit_events` (append-only, hash-chained), `work_items` (leased queue), `persona_snapshots` and
`idempotency_keys`. The same version sequence applies to SQLite and PostgreSQL; latest version is 3.

## APIs added

`/api/v1` grew from 11 to **29 paths / 40 operations** (verified against the OpenAPI schema):
identity (`whoami`, `roles`, users, service accounts, role bindings), organizations (create),
population versions (materialize, list, detail, quality, composition, personas, freeze), cohorts,
and audit (list, chain verification).

Cross-cutting: principal-derived tenant scoping, per-route permission checks, `X-Request-Id` on
every response, stable error objects, `Idempotency-Key` on materialization, opt-in pagination that
preserves the previous bare-array responses, and `/health` plus `/ready`.

## UI added

**None.** The enterprise console is Phase 6 and was not started. The Playground SPA is unchanged;
its five navigation entries, i18n parity across seven locales and typecheck all still pass.

## Test results

| Suite | Result |
|-------|--------|
| Repository tree (`pytest tests/`) | **1,068 passed, 3 skipped** (baseline was 994) |
| Enterprise + security + multitenancy + reproducibility | **128 passed** |
| PostgreSQL store (embedded server) | **2 passed** |
| Curated continuous-integration list | **737 passed, 12 skipped, 2 pre-existing environment failures** |
| Lint (`ruff check .`, pinned 0.15.20 as CI does) | **clean** |
| Survey smoke (no Docker, no API key) | **pass** |

The two curated failures predate this work: one compares file timestamps written inside a single
clock tick, the other asserts `/workspace` while the checkout is bind-mounted at `/data/workspace`.

**Not executed here:** Docker-dependent Web and OS-app smoke paths (no Docker in this environment)
and the `coreset_1m` materialization backend (the one-million-persona corpus is an optional
download that is not installed). Both are coded against the real interfaces and reported as
untested rather than as passing.

New first-class suites: `tests/reproducibility/`, plus additions to `tests/multitenancy/` and
`tests/security/`. Critical invariants now covered by tests: no cross-tenant access (store, HTTP,
work queue, audit, snapshots, idempotency); deterministic population generation for identical
seeds; immutable population snapshots; reproducible cohorts; secrets absent from logs and audit;
audit records for privileged operations; retries not duplicating completed work; and legacy smoke
paths still working.

## Security controls

Pluggable authentication (local tokens, hashed service-account tokens, OIDC/JWT with signature,
issuer, audience and expiry verification); five nested least-privilege roles; tenant scope derived
from the principal so a header cannot widen it; append-only hash-chained audit with tamper
detection; a secret-provider interface with redaction across logs and audit; peppered token
hashing with one-time display; optimistic concurrency; and a strict mode that refuses to serve
unauthenticated. Documented in `SECURITY.md`, `GOVERNANCE.md` and
[THREAT_MODEL.md](THREAT_MODEL.md).

## Performance results

A **10,000-persona** materialization (Full-DAG, three share-based segments, full 1,290-dimension
records) completed in **450 s single-process — ~22 personas/s end to end**, producing 385 MB
(~38.5 KB per persona). Snapshot reads and a 1,000-person cohort draw were both under 10 ms.
Segment counts were exact, duplicates zero, constraint satisfaction 1.0.

Raw Full-DAG *sampling* is ~1,500 rows/s, but end-to-end *materialization* is ~22/s because
per-persona YAML writing and hashing dominate. Capacity planning must use the end-to-end figure.
Formulas and the 1,000,000-person extrapolation (~12.6 h, ~38.5 GB single-process) are in
[IMPLEMENTATION_BASELINE.md](IMPLEMENTATION_BASELINE.md) §6b.

## Known limitations

1. **Phases 3–8 absent**: no experiment lifecycle, cost/trial estimation, approval gate at launch,
   Harbor job generation from an experiment, distributed scheduler, model gateway, hierarchical
   metrics, statistical reporting, console, SDK, `matraix enterprise …` CLI, simulation clock or
   deployment assets. Budget, quota and policy entities exist but are **not yet enforced**.
2. **Materialization is synchronous and single-process.** Beyond ~10⁵ personas it needs sharding
   and object storage.
3. **The `coreset_1m` backend is untested** here (corpus not installed).
4. **Harbor `jobs/` output is not tenant-partitioned**; artifact-level separation is open.
5. **Audit tamper *detection*, not prevention** — append-only storage is a deployment concern.
6. **`RecordQuery.where` filters in Python** after an indexed narrowing; hot fields should become
   real columns.
7. **Upstream data findings** (§6a of the baseline): 32% of checked-in dev-sample personas and 52%
   of Full-DAG-sampled personas already fail the repository's own consistency validator, and the
   dev sample carries an off-catalog `age_bracket` value. Quality therefore reports contradictions
   as a warning with that baseline attached rather than as a failure.

## Migration and backward compatibility

No breaking changes. Existing `/api/v1` routes, payloads and bare-array list responses are
preserved; `MATRIX_ENTERPRISE_API_TOKEN` still works and now denotes a platform admin; with no
credential configured the API stays open for local development exactly as before. Migrations 1–2
are unedited. `matraix.enterprise` public exports remain importable, both stores keep their method
sets, and Harbor, task definitions, job recipes and Playground routes are untouched.

## Local startup

```bash
uv venv --python 3.12
uv pip install -e .
uv pip install pytest pytest-asyncio httpx

uv run matraix smoke application/tasks/example-survey_product-feedback   # no Docker, no key

export MATRIX_ENTERPRISE_STORE=sqlite                 # or postgres + MATRIX_ENTERPRISE_DATABASE_URL
export MATRIX_ENTERPRISE_API_TOKEN=dev-platform-token # omit to stay open locally
uv run matraix enterprise-api --port 8090             # OpenAPI at /docs
```

## Enterprise demo workflow

```bash
H='Authorization: Bearer dev-platform-token'
API=http://127.0.0.1:8090/api/v1

# 1. Tenant + default organization
TENANT=$(curl -s -X POST $API/tenants -H "$H" -H 'Content-Type: application/json' \
  -d '{"name":"Acme Research","slug":"acme"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')

# 2. A 10,000-person shaped population declaration
POP=$(curl -s -X POST $API/population-declarations -H "$H" -H "X-Tenant-Id: $TENANT" \
  -H 'Content-Type: application/json' -d '{
    "name":"Workforce","target_size":10000,"backend":"full_dag",
    "segments":[{"name":"frontline","share":0.55},
                {"name":"professional","share":0.30},
                {"name":"leadership","share":0.15}]}' \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["population_id"])')

# 3. Materialize it reproducibly (idempotent; ~450 s for 10k)
VER=$(curl -s -X POST $API/populations/$POP/versions -H "$H" -H "X-Tenant-Id: $TENANT" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-1' \
  -d '{"seed":2026,"key_dimensions":["age_bracket","region","seniority"]}' \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')

# 4. Inspect quality, composition, and freeze
curl -s $API/population-versions/$VER/quality      -H "$H" -H "X-Tenant-Id: $TENANT"
curl -s $API/population-versions/$VER/composition  -H "$H" -H "X-Tenant-Id: $TENANT"
curl -s -X POST $API/population-versions/$VER/freeze -H "$H" -H "X-Tenant-Id: $TENANT"

# 5. Draw a reproducible cohort (same seed -> same people)
curl -s -X POST $API/population-versions/$VER/cohorts -H "$H" -H "X-Tenant-Id: $TENANT" \
  -H 'Content-Type: application/json' -d '{"name":"wave-1","seed":7,"size":1000}'

# 6. Audit trail and chain verification
curl -s $API/audit-events        -H "$H" -H "X-Tenant-Id: $TENANT"
curl -s $API/audit-events/verify -H "$H" -H "X-Tenant-Id: $TENANT"
```

Steps 7–17 of the brief's definition of done (attach a task, estimate cost, pass budget/policy
checks, launch through Harbor, observe telemetry, resume/retry, inspect hierarchical metrics,
download a report) require Phase 3+ and are **not yet available**. Tenant isolation (step 18) is
demonstrated by `tests/multitenancy/`.

## Next highest-value engineering work

1. **Phase 3 experiment control plane** — `ExperimentSpec` is already modelled, hashed and
   versioned; the gap is planning (trial count, token and cost estimation), budget/quota/policy
   enforcement, the approval gate, and generating a Harbor job from an approved plan. This unlocks
   the majority of the outstanding definition-of-done steps.
2. **Trial capture through Harbor's plugin seam** — `BaseJobPlugin.on_job_start/on_job_end` plus
   `Job.on_trial_ended` map trial results onto the `Trial` records that already exist.
3. **A deterministic offline persona client** for the Harbor survey agent, so end-to-end execution
   is testable in continuous integration without a provider key (the CLI smoke path has one; the
   agent does not).
4. **Sharded materialization** driven by the existing work queue, lifting the population ceiling
   past a single process.
5. **Deployment assets** — compose stack, migration command, health/readiness wiring — so the
   definition-of-done step 1 ("start the stack") is genuinely one command.
