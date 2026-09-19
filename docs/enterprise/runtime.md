# Distributed runtime (planes and workers)

Phase 5 adds **control / data / execution planes** and a **worker
abstraction**. Harbor `Job` / `Trial` remain the simulation engine.
`matraix run` defaults are unchanged. Default policy remains `SANDBOX_ONLY`.

## Planes

| Plane | In | Out | Must not |
|-------|----|-----|----------|
| **Control** | Tenant-scoped launch (`execute_experiment`) | `WorkRequest` to the execution plane | Import LiteLLM or Docker/K8s SDKs |
| **Data** | Tenant-scoped artifacts and events | Stored blobs + event history | Cloud-vendor fields; silent persistent memory |
| **Execution** | `WorkRequest` holding the existing `Experiment` | `ExecutionRecord` | Own IAM or billing; construct `harbor.Job` |

`EnterpriseRuntime` wires the three. The same `Experiment` type is submitted
for every worker kind.

## Workers

| Kind | Status |
|------|--------|
| `local` | Implemented. Sandbox path only. |
| `docker` | Stub (same `Experiment`; no Docker SDK) |
| `kubernetes` | Stub (name only; cloud-neutral) |
| `queue` | Stub |
| `batch` | Stub |

Env: `MATRIX_ENTERPRISE_WORKER` (default `local`).

The **local sandbox path**:

1. Evaluate policy (`execute_experiment`, destination `sandbox`).
2. If `task_path` is set, map onto a Harbor **job document** (`map_experiment_to_harbor_job`) — not a `harbor.Job` instance.
3. Call the model gateway sandbox completer.
4. Schedule logical trial slots on `SimulationClock` (capped) and publish events.
5. Persist `ExecutionRecord`, artifacts, and events.
6. Record Phase 6 observability (OTel-shaped trace, hierarchical metrics,
   evaluation bundle, optional failure class) as additional artifacts. See
   [telemetry.md](telemetry.md).

`WorldState` exists but stays **disabled** unless `enable_world_state` is
explicit. That is not Harbor environment state and is not persistent memory.

## Events

Additive to Harbor trials: `execution_submitted`, `policy_evaluated`,
`worker_started`, `trial_scheduled`, `clock_tick`, `artifact_written`,
`execution_completed` / `denied` / `held`, `worker_unavailable`.

`EventBus` is in-process; the data plane persists the same events.

## API

See [api.md](api.md): `GET /api/v1/workers`, `POST /api/v1/experiments/{id}/execute`,
`/api/v1/executions`, `/api/v1/events`, plus telemetry
`/trace` `/metrics` `/evaluation` `/failures`.
