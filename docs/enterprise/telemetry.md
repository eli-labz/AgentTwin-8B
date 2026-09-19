# Telemetry, metrics, and evaluation

Phase 6 adds **OpenTelemetry-shaped traces**, **hierarchical metrics**, a
**failure taxonomy**, and a **deterministic-first evaluation SDK**. Dashboards
are deferred to Phase 7–8; this slice persists JSON artifacts and exposes them
on `/api/v1`. `matraix run` defaults are unchanged. Default policy remains
`SANDBOX_ONLY`.

No OpenTelemetry SDK, LiteLLM, or Harbor `Job` is imported. Span JSON uses the
same field names an exporter would (`trace_id`, `span_id`, `resource_spans`,
`scope_spans`, `status.code`).

## Limitation (required)

**Synthetic persona outputs and metrics are simulation parameters. They are
not equivalent to human research, usability testing, employee consultation,
or customer research.** Every evaluation bundle sets
`synthetic_equivalent_to_human_research: false` and
`recommended_human_validation: true`.

## Traces

`InMemoryTracer` (`matraix.enterprise.telemetry`) records spans for each
sandbox execution:

| Span | Role |
|------|------|
| `enterprise.execute` | Root; latency, tenant, experiment, execution |
| `enterprise.policy` | Policy decision + `policy.evaluated` event |
| `enterprise.complete` | Tokens / cost |
| `enterprise.evaluate` | Verification result |

Common attributes: `tenant_id`, `experiment_id`, `execution_id`, `persona_id`,
`task`, `model`, `seed`, `code_version`, `tokens`, `cost_usd`, `tool_calls`,
`policy.decision`, `verification.result`.

The snapshot is stored as a tenant-scoped **artifact** (`kind=trace`). Isolation
is the data plane’s, not a vendor backend’s.

## Hierarchical metrics

Levels (low → high): `step` → `task` → `session` → `persona` → `cohort` →
`population` → `experiment` → `enterprise`.

`MetricsRegistry` aggregates mean / min / max and a normal-approximation 95%
CI when `n >= 2`. Points carry `segments` and `provenance` (seed, code
version, execution / experiment ids). Aggregates always include
`SYNTHETIC_METRIC_LIMITATION`.

## Failure taxonomy

Structured class on failed simulations (`FailureClass`):

`PERCEPTION_FAILURE`, `INSTRUCTION_FAILURE`, `REASONING_FAILURE`,
`TOOL_FAILURE`, `KNOWLEDGE_FAILURE`, `POLICY_FAILURE`, `EXECUTION_FAILURE`,
`VERIFICATION_FAILURE`, `INTERACTION_FAILURE`, `LATENCY_FAILURE`,
`ESCALATION_FAILURE`, `ENVIRONMENT_FAILURE`.

Local DENY → `POLICY_FAILURE`. Held-for-approval → `ESCALATION_FAILURE`.
Remote worker stubs → `ENVIRONMENT_FAILURE`. Deterministic verifier miss →
`VERIFICATION_FAILURE` unless a more specific keyword matches.

## Evaluation SDK

`evaluate_execution` is the composition point:

1. **Deterministic verifiers first** (pass/fail authority). The sandbox
   verifier fails if the path replaced `harbor.Job`, was denied, held,
   unavailable, or is missing a completion / document artifact.
2. **LLM judges are supplemental only.** `supplemental_llm_judge` does not
   call a provider. `used_for_pass_fail` is forced `false`.
3. Never present synthetic outputs as human research.

Re-run via `POST /api/v1/executions/{id}/evaluate` (`include_llm_judge` is
optional and still not authoritative).

## API

Tenant header `X-Tenant-Id` is required. Cross-tenant reads are `404` (the
execution id is constructed in the caller’s tenant).

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/v1/executions/{id}/trace` | OTel-shaped snapshot |
| `GET` | `/api/v1/executions/{id}/metrics` | Points + hierarchy + aggregates |
| `GET` | `/api/v1/executions/{id}/evaluation` | Stored evaluation bundle |
| `POST` | `/api/v1/executions/{id}/evaluate` | Re-run SDK (optional LLM-judge note) |
| `GET` | `/api/v1/failures` | Failure artifacts; optional `?execution_id=` |

Successful local execute also copies `result.trace_id` and a short evaluation
summary onto `ExecutionRecord.result`. Artifacts of kinds `trace`, `metrics`,
`evaluation`, and (on failure) `failure` are written next to the existing
`harbor_job` / `completion` blobs.

## Dashboards

No console charts in this phase. Phase 7 (enterprise console) and Phase 8
(executive analytics) consume these artifacts. Until then, use `/docs` and the
JSON endpoints above.

## Modules

| Module | Role |
|--------|------|
| `matraix.enterprise.telemetry` | Trace ids, spans, taxonomy, `classify_failure` |
| `matraix.enterprise.metrics` | Hierarchical registry + CI |
| `matraix.enterprise.evaluation` | Deterministic + supplemental judge |
| `matraix.enterprise.observability` | Wire snapshots for the local worker |
