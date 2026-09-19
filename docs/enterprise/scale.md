# Scale path — 10 to 1,000,000+ simulated users

This is the Phase 10 scale document. It does **not** assume one machine.
The Phase 5 worker abstraction is the seam: the same `Experiment` type is
submitted to every worker kind. Only `local` is implemented today.

Synthetic load is **not** human research. Benchmarks set
`synthetic_equivalent_to_human_research: false`.

## How to run the synthetic bench

```bash
# CI-sized (default): in-memory store, sandbox model, no live provider
uv run matraix enterprise-bench

# Larger local probe
uv run matraix enterprise-bench --personas 200 --tasks 20 --store sqlite \
  --db /tmp/enterprise-bench.sqlite --format json -o /tmp/bench.json
```

`matraix run` defaults are unchanged. Default policy remains `SANDBOX_ONLY`.

The report (`EnterpriseBenchmark.v1`) includes:

| Field | Meaning |
|-------|---------|
| `personas_per_sec` | Population row writes / second |
| `tasks_per_sec` | Local sandbox `execute_experiment` / second |
| `queue_latency_ms` | In-process local worker latency (not a real queue) |
| `queue_stub_reject_ms` | Time to reject `WorkerKind.QUEUE` (stub) |
| `model_latency_ms` | Phase 4 sandbox completer (mocked; no live provider) |
| `db_latency_ms` | `list_populations` round-trip |
| `telemetry_overhead_ms` | Serialize stored artifacts (`to_dict`) |
| `rss_mb` / `cpu_user_s` | Process RSS and user CPU for the run |
| `cost_per_persona_usd` / `cost_per_task_usd` | From `estimate_experiment_cost` |

Defaults stay small so CI finishes quickly. A passing bench is **not** a
1M soak and is **not** a certified SLA.

## Tiers (no one-machine assumption)

| Simulated users | Control plane | Execution | What is real today |
|-----------------|---------------|-----------|--------------------|
| 10 | In-memory or SQLite on one process | `WorkerKind.LOCAL` sandbox | Implemented. `enterprise-bench` default. |
| 100–1k | SQLite file or one API replica | Local worker **or** existing Harbor `matraix run` / Playground `computeFamily=local` | Local worker + Harbor local path. |
| 10k | SQLite or operator-supplied shared SQL (not in-repo) | Many **local** API replicas are the wrong tool — submit Harbor jobs | Population **declaration** of 10k is validated (Phase 2). Materializing 10k YAML personas is still the existing `persona/synthesis` / Persona 1M path. |
| 100k–1M+ | Shared durable store + object storage for artifacts (operator) | Remote workers: Docker / Kubernetes / queue / batch | **Named stubs** (`worker_catalog()` reports `available: false`). Use existing Harbor `computeFamily=modal\|gcp` for large Harbor jobs — that path is **not** tenant-prefixed on disk. |

The 1M **coreset** already exists as a Hugging Face dataset. Importing it
does not require the enterprise control plane. Enterprise populations
**declare** a shape; they do not rewrite synthesis.

## Worker abstraction (Phase 5)

```text
Control plane  →  WorkRequest(experiment, worker_kind)
Execution plane → local sandbox  |  docker stub  |  k8s stub  |  queue stub  |  batch stub
Data plane     →  tenant-scoped artifacts / events / reports
```

Scale-out means **more workers of a real kind**, not a bigger
`enterprise-api` process. When Docker / Kubernetes / queue / batch
workers are wired, they must:

1. Accept the same `Experiment` record.
2. Call `evaluate_policy` first (`SANDBOX_ONLY` default).
3. Keep tenant IDs on every artifact and trace.
4. Stay cloud-neutral in domain types (no `Tenant.gcp_project`).

Until those workers exist, do not claim a 1M enterprise soak.

## Existing Harbor large-run path

For many-persona Harbor jobs (not the enterprise control plane), see
[large-scale-runs.md](../environment/large-scale-runs.md). That path uses
Playground `computeFamily` (`local` / `modal` / `gcp`) and still writes
`jobs/<job_name>/` **without** tenant prefixes. It is the current way to
fan out trials. It is **not** a substitute for tenant isolation.

## What this document does not claim

- Remote enterprise workers are **stubs**.
- Harbor filesystem tenancy is **not** implemented.
- `enterprise-bench` is a synthetic sandbox probe, not a cluster soak.
- Cost figures are estimates from overridable token/USD rates, not invoices.
