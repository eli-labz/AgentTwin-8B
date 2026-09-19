"""Synthetic enterprise load measurements.

Measures personas/sec, tasks/sec, queue / model / DB latency, telemetry
overhead, RSS, and cost/persona. Uses the sandbox model path — no live
provider and no ``matraix run`` change. Default sizes stay small so CI
finishes quickly.
"""

from __future__ import annotations

import json
import resource
import time
from dataclasses import dataclass, field
from typing import Any

from matraix.enterprise.entities import Experiment, Population
from matraix.enterprise.experiment_launch import estimate_experiment_cost
from matraix.enterprise.ids import EntityKind, ExperimentId, PopulationId, new_id
from matraix.enterprise.model_gateway import ModelRequest, complete_model
from matraix.enterprise.population_builder import (
    GenerationBackend,
    PopulationSegment,
    build_population_declaration,
)
from matraix.enterprise.repositories import InMemoryEnterpriseStore
from matraix.enterprise.runtime import EnterpriseRuntime, WorkerKind, WorkRequest
from matraix.enterprise.store import create_tenant_with_default_org, open_enterprise_store

SCHEMA_VERSION = "EnterpriseBenchmark.v1"


def _now() -> float:
    return time.perf_counter()


def _rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # ru_maxrss is KB on Linux, bytes on macOS. Treat values > 10M as bytes.
    raw = float(usage.ru_maxrss)
    if raw > 10_000_000:
        return raw / (1024 * 1024)
    return raw / 1024.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


@dataclass
class BenchmarkReport:
    schema_version: str = SCHEMA_VERSION
    personas: int = 0
    tasks: int = 0
    store: str = "memory"
    personas_per_sec: float = 0.0
    tasks_per_sec: float = 0.0
    queue_latency_ms: float = 0.0
    model_latency_ms: float = 0.0
    db_latency_ms: float = 0.0
    telemetry_overhead_ms: float = 0.0
    rss_mb: float = 0.0
    cpu_user_s: float = 0.0
    cost_per_persona_usd: float = 0.0
    cost_per_task_usd: float = 0.0
    queue_stub_reject_ms: float = 0.0
    duration_s: float = 0.0
    default_policy: str = "SANDBOX_ONLY"
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "personas": self.personas,
            "tasks": self.tasks,
            "store": self.store,
            "personas_per_sec": self.personas_per_sec,
            "tasks_per_sec": self.tasks_per_sec,
            "queue_latency_ms": self.queue_latency_ms,
            "queue_stub_reject_ms": self.queue_stub_reject_ms,
            "model_latency_ms": self.model_latency_ms,
            "db_latency_ms": self.db_latency_ms,
            "telemetry_overhead_ms": self.telemetry_overhead_ms,
            "rss_mb": self.rss_mb,
            "cpu_user_s": self.cpu_user_s,
            "cost_per_persona_usd": self.cost_per_persona_usd,
            "cost_per_task_usd": self.cost_per_task_usd,
            "duration_s": self.duration_s,
            "default_policy": self.default_policy,
            "notes": list(self.notes),
            "synthetic_equivalent_to_human_research": False,
        }


def run_benchmark(
    *,
    personas: int = 10,
    tasks: int = 2,
    store: str = "memory",
    db_path: str | None = None,
) -> BenchmarkReport:
    """Run a synthetic load against the local sandbox worker."""
    n_personas = max(1, int(personas))
    n_tasks = max(1, int(tasks))
    backend = (store or "memory").strip().lower()
    if backend == "sqlite":
        repository = open_enterprise_store(
            backend="sqlite", path=db_path or ":memory:"
        )
    else:
        repository = InMemoryEnterpriseStore()
        backend = "memory"

    tenant, org = create_tenant_with_default_org(
        repository, name="Bench", slug=f"bench-{new_id(EntityKind.TENANT)[-8:]}"
    )
    runtime = EnterpriseRuntime(repository)
    cpu_before = resource.getrusage(resource.RUSAGE_SELF).ru_utime
    wall_started = _now()

    started = _now()
    for index in range(n_personas):
        repository.put_population(
            Population(
                id=PopulationId(tenant.id, new_id(EntityKind.POPULATION)),
                tenant_id=tenant.id,
                organization_id=org.id,
                name=f"seg-{index}",
                target_size=1,
            )
        )
    persona_seconds = max(_now() - started, 1e-9)

    declaration = build_population_declaration(
        tenant_id=tenant.id,
        organization_id=org.id,
        population_id=PopulationId(tenant.id, new_id(EntityKind.POPULATION)),
        target_size=n_personas,
        backend=GenerationBackend.TREIVER,
        segments=(PopulationSegment(name="all", count=n_personas),),
    )
    repository.put_population(
        Population(
            id=declaration.population_id,
            tenant_id=tenant.id,
            organization_id=org.id,
            name="bench-declared",
            target_size=n_personas,
        )
    )
    repository.put_population_declaration(declaration)

    db_samples: list[float] = []
    for _ in range(min(20, n_personas)):
        tick = _now()
        repository.list_populations(tenant.id)
        db_samples.append((_now() - tick) * 1000)

    model_samples: list[float] = []
    for _ in range(n_tasks):
        tick = _now()
        complete_model(
            ModelRequest(messages=[{"role": "user", "content": "bench"}]),
            tenant_id=tenant.id,
        )
        model_samples.append((_now() - tick) * 1000)

    queue_samples: list[float] = []
    exec_started = _now()
    last_experiment = None
    for index in range(n_tasks):
        experiment = repository.put_experiment(
            Experiment(
                id=ExperimentId(tenant.id, new_id(EntityKind.EXPERIMENT)),
                tenant_id=tenant.id,
                organization_id=org.id,
                hypothesis=f"H{index}",
                objective="bench",
                random_seed=42,
                sample_size=1,
            )
        )
        last_experiment = experiment
        tick = _now()
        runtime.execute_experiment(tenant.id, experiment.id)
        queue_samples.append((_now() - tick) * 1000)
    task_seconds = max(_now() - exec_started, 1e-9)

    stub_started = _now()
    if last_experiment is not None:
        runtime.execution.submit(
            WorkRequest(experiment=last_experiment, worker_kind=WorkerKind.QUEUE)
        )
    stub_ms = (_now() - stub_started) * 1000

    telemetry_ms = 0.0
    if last_experiment is not None:
        artifacts = repository.list_artifacts(tenant.id)
        tick = _now()
        _ = [item.to_dict() for item in artifacts]
        telemetry_ms = (_now() - tick) * 1000

    estimate = estimate_experiment_cost(
        last_experiment
        or Experiment(
            id=ExperimentId(tenant.id, new_id(EntityKind.EXPERIMENT)),
            tenant_id=tenant.id,
            organization_id=org.id,
            hypothesis="H",
            objective="O",
            sample_size=n_personas,
        ),
        population_target=n_personas,
    )
    usage = resource.getrusage(resource.RUSAGE_SELF)
    notes = (
        "Synthetic sandbox load — not a 1M soak and not human research.",
        "Model latency is the Phase 4 sandbox completer (no live provider).",
        "queue_latency_ms is local in-process execute; remote workers are stubs.",
        "Remote docker/kubernetes/queue/batch workers remain stubs.",
        f"Queue stub reject measured at {stub_ms:.3f} ms.",
        "Default policy SANDBOX_ONLY. matraix run is unchanged.",
    )
    return BenchmarkReport(
        personas=n_personas,
        tasks=n_tasks,
        store=backend,
        personas_per_sec=n_personas / persona_seconds,
        tasks_per_sec=n_tasks / task_seconds,
        queue_latency_ms=_mean(queue_samples),
        queue_stub_reject_ms=stub_ms,
        model_latency_ms=_mean(model_samples),
        db_latency_ms=_mean(db_samples),
        telemetry_overhead_ms=telemetry_ms,
        rss_mb=_rss_mb(),
        cpu_user_s=max(float(usage.ru_utime) - cpu_before, 0.0),
        cost_per_persona_usd=(
            estimate.estimated_cost_usd / n_personas if n_personas else 0.0
        ),
        cost_per_task_usd=(
            estimate.estimated_cost_usd / n_tasks if n_tasks else 0.0
        ),
        duration_s=_now() - wall_started,
        notes=notes,
    )


def format_benchmark_json(report: BenchmarkReport) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n"


def format_benchmark_text(report: BenchmarkReport) -> str:
    payload = report.to_dict()
    lines = [
        f"Enterprise benchmark ({payload['schema_version']})",
        f"  store={payload['store']} personas={payload['personas']} tasks={payload['tasks']}",
        f"  personas/sec={payload['personas_per_sec']:.2f}",
        f"  tasks/sec={payload['tasks_per_sec']:.2f}",
        f"  queue_latency_ms={payload['queue_latency_ms']:.3f} (local worker)",
        f"  queue_stub_reject_ms={payload['queue_stub_reject_ms']:.3f}",
        f"  model_latency_ms={payload['model_latency_ms']:.3f} (sandbox mock)",
        f"  db_latency_ms={payload['db_latency_ms']:.3f}",
        f"  telemetry_overhead_ms={payload['telemetry_overhead_ms']:.3f}",
        f"  rss_mb={payload['rss_mb']:.1f} cpu_user_s={payload['cpu_user_s']:.3f}",
        f"  cost/persona=${payload['cost_per_persona_usd']:.6f}",
        f"  cost/task=${payload['cost_per_task_usd']:.6f}",
        f"  policy={payload['default_policy']}",
        "  synthetic_equivalent_to_human_research: false",
    ]
    for note in payload["notes"]:
        lines.append(f"  - {note}")
    return "\n".join(lines) + "\n"
