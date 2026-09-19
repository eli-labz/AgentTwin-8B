"""Control, data, and execution planes plus a worker abstraction.

The local worker runs a **sandbox experiment path**: policy evaluation, Harbor
job-document mapping (not ``harbor.Job``), model-gateway sandbox complete, and
artifacts/events. Docker / Kubernetes / queue / batch are named stubs.

The same :class:`~matraix.enterprise.entities.Experiment` type is submitted
to every worker. Domain types stay cloud-neutral. ``matraix run`` is unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from matraix.enterprise.entities import Experiment
from matraix.enterprise.errors import (
    EnterpriseSchemaError,
    EntityNotFoundError,
    WorkerNotAvailableError,
)
from matraix.enterprise.events import (
    EventBus,
    EventKind,
    EnterpriseEvent,
    SimulationClock,
    WorldState,
    new_event,
)
from matraix.enterprise.experiment_launch import (
    map_experiment_to_harbor_job,
    resolve_trial_count,
)
from matraix.enterprise.ids import (
    ArtifactId,
    EntityKind,
    ExecutionId,
    ExperimentId,
    TenantId,
    new_id,
)
from matraix.enterprise.model_gateway import (
    ModelPolicy,
    ModelRequest,
    complete_model,
    resolve_model_policy,
)
from matraix.enterprise.policy import (
    DataClassification,
    PolicyDecision,
    PolicyRequest,
    evaluate_policy,
)
from matraix.enterprise.repositories import EnterpriseRepository

WORKER_ENV = "MATRIX_ENTERPRISE_WORKER"
DEFAULT_WORKER = "local"
_MAX_SCHEDULED_TICKS = 8


class WorkerKind(str, Enum):
    LOCAL = "local"
    DOCKER = "docker"
    KUBERNETES = "kubernetes"
    QUEUE = "queue"
    BATCH = "batch"


class ExecutionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    DENIED = "denied"
    HELD = "held"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class PlaneName(str, Enum):
    CONTROL = "control"
    DATA = "data"
    EXECUTION = "execution"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_worker_kind(value: str | WorkerKind | None) -> WorkerKind:
    raw = (value.value if isinstance(value, WorkerKind) else value) or os.environ.get(
        WORKER_ENV, DEFAULT_WORKER
    )
    text = str(raw).strip().lower() or DEFAULT_WORKER
    try:
        return WorkerKind(text)
    except ValueError as exc:
        raise EnterpriseSchemaError(
            f"unknown worker {text!r}; use local, docker, kubernetes, queue, batch"
        ) from exc


@dataclass(frozen=True, slots=True)
class WorkerCapabilities:
    kind: WorkerKind
    available: bool
    sandbox_only: bool
    description: str
    targets: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "available": self.available,
            "sandbox_only": self.sandbox_only,
            "description": self.description,
            "targets": list(self.targets),
        }


WORKER_CATALOG: tuple[WorkerCapabilities, ...] = (
    WorkerCapabilities(
        kind=WorkerKind.LOCAL,
        available=True,
        sandbox_only=True,
        description="In-process sandbox path. Does not construct harbor.Job.",
        targets=("host", "sandbox"),
    ),
    WorkerCapabilities(
        kind=WorkerKind.DOCKER,
        available=False,
        sandbox_only=True,
        description="Stub. Same Experiment type; Docker SDK is not imported.",
        targets=("container",),
    ),
    WorkerCapabilities(
        kind=WorkerKind.KUBERNETES,
        available=False,
        sandbox_only=True,
        description="Stub. Cloud-neutral name only; no cluster client.",
        targets=("pod",),
    ),
    WorkerCapabilities(
        kind=WorkerKind.QUEUE,
        available=False,
        sandbox_only=True,
        description="Stub. Queue adapter is not wired.",
        targets=("queue",),
    ),
    WorkerCapabilities(
        kind=WorkerKind.BATCH,
        available=False,
        sandbox_only=True,
        description="Stub. Batch adapter is not wired.",
        targets=("batch",),
    ),
)


def worker_catalog() -> list[dict[str, Any]]:
    return [item.to_dict() for item in WORKER_CATALOG]


@dataclass(frozen=True, slots=True)
class Artifact:
    id: ArtifactId
    tenant_id: TenantId
    kind: str
    name: str
    content: dict[str, Any]
    execution_id: ExecutionId | None = None
    experiment_id: ExperimentId | None = None
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if self.id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("artifact id tenant mismatch")
        if self.execution_id is not None and self.execution_id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("artifact execution is not in this tenant")
        if self.experiment_id is not None and self.experiment_id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("artifact experiment is not in this tenant")
        object.__setattr__(self, "kind", str(self.kind or "").strip() or "blob")
        object.__setattr__(self, "name", str(self.name or "").strip() or self.kind)
        object.__setattr__(self, "content", dict(self.content or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id.value,
            "tenant_id": self.tenant_id.value,
            "kind": self.kind,
            "name": self.name,
            "content": dict(self.content),
            "execution_id": self.execution_id.value if self.execution_id else None,
            "experiment_id": self.experiment_id.value if self.experiment_id else None,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class WorkRequest:
    """Dispatch envelope. Holds the existing Experiment — no new domain type."""

    experiment: Experiment
    worker_kind: WorkerKind = WorkerKind.LOCAL
    persona_paths: tuple[str, ...] = ()
    approved: bool = False
    enable_world_state: bool = False
    model_provider: str | None = None
    population_target: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "worker_kind", parse_worker_kind(self.worker_kind))
        object.__setattr__(self, "persona_paths", tuple(self.persona_paths))


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    id: ExecutionId
    tenant_id: TenantId
    experiment_id: ExperimentId
    worker_kind: WorkerKind
    status: ExecutionStatus
    decision: PolicyDecision
    plane: PlaneName = PlaneName.EXECUTION
    reasons: tuple[str, ...] = ()
    harbor_job_name: str | None = None
    trial_slots: int = 0
    concurrency: int = 1
    artifact_ids: tuple[str, ...] = ()
    result: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if self.id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("execution id tenant mismatch")
        if self.experiment_id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("execution experiment is not in this tenant")
        object.__setattr__(self, "worker_kind", parse_worker_kind(self.worker_kind))
        status = (
            self.status
            if isinstance(self.status, ExecutionStatus)
            else ExecutionStatus(self.status)
        )
        object.__setattr__(self, "status", status)
        decision = (
            self.decision
            if isinstance(self.decision, PolicyDecision)
            else PolicyDecision(self.decision)
        )
        object.__setattr__(self, "decision", decision)
        plane = self.plane if isinstance(self.plane, PlaneName) else PlaneName(self.plane)
        object.__setattr__(self, "plane", plane)
        object.__setattr__(self, "reasons", tuple(self.reasons))
        object.__setattr__(self, "artifact_ids", tuple(self.artifact_ids))
        object.__setattr__(self, "result", dict(self.result or {}))
        object.__setattr__(self, "trial_slots", int(self.trial_slots))
        object.__setattr__(self, "concurrency", max(1, int(self.concurrency)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id.value,
            "tenant_id": self.tenant_id.value,
            "experiment_id": self.experiment_id.value,
            "worker_kind": self.worker_kind.value,
            "status": self.status.value,
            "decision": self.decision.value,
            "plane": self.plane.value,
            "reasons": list(self.reasons),
            "harbor_job_name": self.harbor_job_name,
            "trial_slots": self.trial_slots,
            "concurrency": self.concurrency,
            "artifact_ids": list(self.artifact_ids),
            "result": dict(self.result),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


def execution_from_dict(payload: dict[str, Any]) -> ExecutionRecord:
    tenant = TenantId(payload["tenant_id"])
    return ExecutionRecord(
        id=ExecutionId(tenant, payload["id"]),
        tenant_id=tenant,
        experiment_id=ExperimentId(tenant, payload["experiment_id"]),
        worker_kind=payload["worker_kind"],
        status=payload["status"],
        decision=payload["decision"],
        plane=payload.get("plane", PlaneName.EXECUTION),
        reasons=tuple(payload.get("reasons") or ()),
        harbor_job_name=payload.get("harbor_job_name"),
        trial_slots=int(payload.get("trial_slots") or 0),
        concurrency=int(payload.get("concurrency") or 1),
        artifact_ids=tuple(payload.get("artifact_ids") or ()),
        result=dict(payload.get("result") or {}),
        created_at=datetime.fromisoformat(payload["created_at"]),
        updated_at=datetime.fromisoformat(payload["updated_at"]),
    )


def artifact_from_dict(payload: dict[str, Any]) -> Artifact:
    tenant = TenantId(payload["tenant_id"])
    execution = (
        ExecutionId(tenant, payload["execution_id"]) if payload.get("execution_id") else None
    )
    experiment = (
        ExperimentId(tenant, payload["experiment_id"])
        if payload.get("experiment_id")
        else None
    )
    created = payload.get("created_at")
    return Artifact(
        id=ArtifactId(tenant, payload["id"]),
        tenant_id=tenant,
        kind=payload["kind"],
        name=payload["name"],
        content=dict(payload.get("content") or {}),
        execution_id=execution,
        experiment_id=experiment,
        created_at=datetime.fromisoformat(created) if created else _utcnow(),
    )


class Worker(Protocol):
    kind: WorkerKind

    def capabilities(self) -> WorkerCapabilities: ...

    def submit(self, request: WorkRequest, *, context: "WorkerContext") -> ExecutionRecord: ...


@dataclass
class WorkerContext:
    store: EnterpriseRepository
    bus: EventBus
    clock: SimulationClock
    world: WorldState
    policy: ModelPolicy | None = None


def _capabilities(kind: WorkerKind) -> WorkerCapabilities:
    for item in WORKER_CATALOG:
        if item.kind is kind:
            return item
    raise WorkerNotAvailableError(kind.value)


def _new_execution_id(tenant_id: TenantId) -> ExecutionId:
    return ExecutionId(tenant_id, new_id(EntityKind.EXECUTION))


def _new_artifact(
    tenant_id: TenantId,
    *,
    kind: str,
    name: str,
    content: dict[str, Any],
    execution_id: ExecutionId,
    experiment_id: ExperimentId,
) -> Artifact:
    return Artifact(
        id=ArtifactId(tenant_id, new_id(EntityKind.ARTIFACT)),
        tenant_id=tenant_id,
        kind=kind,
        name=name,
        content=content,
        execution_id=execution_id,
        experiment_id=experiment_id,
    )


def _publish(
    context: WorkerContext,
    tenant_id: TenantId,
    kind: EventKind,
    *,
    execution_id: ExecutionId,
    experiment_id: ExperimentId,
    payload: dict[str, Any] | None = None,
) -> EnterpriseEvent:
    event = new_event(
        tenant_id,
        kind,
        payload=payload,
        experiment_id=experiment_id,
        execution_id=execution_id,
        tick=context.clock.now(),
    )
    context.bus.publish(event)
    context.store.put_event(event)
    return event


class LocalSandboxWorker:
    """Sandbox execution path. Never imports or constructs ``harbor.Job``."""

    kind = WorkerKind.LOCAL

    def capabilities(self) -> WorkerCapabilities:
        return _capabilities(WorkerKind.LOCAL)

    def submit(self, request: WorkRequest, *, context: WorkerContext) -> ExecutionRecord:
        experiment = request.experiment
        tenant_id = experiment.tenant_id
        execution_id = _new_execution_id(tenant_id)
        policy = resolve_model_policy(tenant_id, context.policy)
        evaluation = evaluate_policy(
            PolicyRequest(
                tenant_id=tenant_id,
                action="execute_experiment",
                resource=f"experiment.{experiment.id.value}",
                model_provider=request.model_provider or "sandbox",
                data_classification=experiment.data_classification,
                destination="sandbox",
                approved=request.approved,
            ),
            policy,
        )
        concurrency = experiment.execution_budget.max_concurrency or 1
        trial_slots = resolve_trial_count(
            experiment, population_target=request.population_target
        )
        now = _utcnow()
        base = dict(
            id=execution_id,
            tenant_id=tenant_id,
            experiment_id=experiment.id,
            worker_kind=WorkerKind.LOCAL,
            plane=PlaneName.EXECUTION,
            trial_slots=trial_slots,
            concurrency=concurrency,
            created_at=now,
            updated_at=now,
        )
        _publish(
            context,
            tenant_id,
            EventKind.EXECUTION_SUBMITTED,
            execution_id=execution_id,
            experiment_id=experiment.id,
            payload={"worker_kind": WorkerKind.LOCAL.value},
        )
        _publish(
            context,
            tenant_id,
            EventKind.POLICY_EVALUATED,
            execution_id=execution_id,
            experiment_id=experiment.id,
            payload={"decision": evaluation.decision.value, "reasons": list(evaluation.reasons)},
        )
        if evaluation.decision is PolicyDecision.DENY:
            record = ExecutionRecord(
                **base,
                status=ExecutionStatus.DENIED,
                decision=evaluation.decision,
                reasons=evaluation.reasons,
                result={"denied": True},
            )
            _publish(
                context,
                tenant_id,
                EventKind.EXECUTION_DENIED,
                execution_id=execution_id,
                experiment_id=experiment.id,
                payload={"reasons": list(evaluation.reasons)},
            )
            return context.store.put_execution(record)
        if evaluation.decision is PolicyDecision.ALLOW_WITH_APPROVAL and not request.approved:
            record = ExecutionRecord(
                **base,
                status=ExecutionStatus.HELD,
                decision=evaluation.decision,
                reasons=evaluation.reasons,
                result={"held_for_approval": True},
            )
            _publish(
                context,
                tenant_id,
                EventKind.EXECUTION_HELD,
                execution_id=execution_id,
                experiment_id=experiment.id,
                payload={"reasons": list(evaluation.reasons)},
            )
            return context.store.put_execution(record)

        if request.enable_world_state:
            context.world.enable(tenant_id)
        _publish(
            context,
            tenant_id,
            EventKind.WORKER_STARTED,
            execution_id=execution_id,
            experiment_id=experiment.id,
            payload={"sandbox": True},
        )
        artifacts: list[Artifact] = []
        harbor_job_name = None
        mapped: dict[str, Any] | None = None
        if experiment.task_path:
            mapped = map_experiment_to_harbor_job(
                experiment, persona_paths=list(request.persona_paths)
            )
            harbor_job_name = mapped["harbor_job"]["job_name"]
            artifacts.append(
                _new_artifact(
                    tenant_id,
                    kind="harbor_job",
                    name="harbor-job",
                    content=mapped,
                    execution_id=execution_id,
                    experiment_id=experiment.id,
                )
            )
        completion = complete_model(
            ModelRequest(
                tenant_id=tenant_id,
                messages=(
                    {
                        "role": "user",
                        "content": (
                            f"sandbox execute {experiment.id.value}: {experiment.hypothesis}"
                        ),
                    },
                ),
                action="complete",
                resource="runtime.sandbox",
                data_classification=experiment.data_classification,
                destination="sandbox",
            ),
            policy,
        )
        artifacts.append(
            _new_artifact(
                tenant_id,
                kind="completion",
                name="sandbox-complete",
                content=completion.to_dict(),
                execution_id=execution_id,
                experiment_id=experiment.id,
            )
        )
        ticks = min(max(1, trial_slots), _MAX_SCHEDULED_TICKS)
        for index in range(ticks):
            context.clock.advance(1)
            _publish(
                context,
                tenant_id,
                EventKind.TRIAL_SCHEDULED,
                execution_id=execution_id,
                experiment_id=experiment.id,
                payload={"slot": index, "of": trial_slots, "harbor_trial": False},
            )
            _publish(
                context,
                tenant_id,
                EventKind.CLOCK_TICK,
                execution_id=execution_id,
                experiment_id=experiment.id,
                payload={"tick": context.clock.now()},
            )
        stored_ids: list[str] = []
        for artifact in artifacts:
            context.store.put_artifact(artifact)
            stored_ids.append(artifact.id.value)
            _publish(
                context,
                tenant_id,
                EventKind.ARTIFACT_WRITTEN,
                execution_id=execution_id,
                experiment_id=experiment.id,
                payload={"artifact_id": artifact.id.value, "kind": artifact.kind},
            )
        result = {
            "sandbox": True,
            "replaced_harbor_job": False,
            "mapped_harbor_document": mapped is not None,
            "completion_provider": completion.provider,
            "world_state_enabled": context.world.is_enabled(tenant_id),
            "clock_tick": context.clock.now(),
        }
        record = ExecutionRecord(
            **base,
            status=ExecutionStatus.COMPLETED,
            decision=evaluation.decision,
            reasons=evaluation.reasons + ("local sandbox worker; harbor.Job not constructed",),
            harbor_job_name=harbor_job_name,
            artifact_ids=tuple(stored_ids),
            result=result,
        )
        stored = context.store.put_execution(record)
        _publish(
            context,
            tenant_id,
            EventKind.EXECUTION_COMPLETED,
            execution_id=execution_id,
            experiment_id=experiment.id,
            payload={"status": stored.status.value, "harbor_job_name": harbor_job_name},
        )
        return stored


class RemoteWorkerStub:
    """Named remote target. Accepts the same Experiment; adapter is not wired."""

    def __init__(self, kind: WorkerKind) -> None:
        if kind is WorkerKind.LOCAL:
            raise EnterpriseSchemaError("local is not a remote stub")
        self.kind = kind

    def capabilities(self) -> WorkerCapabilities:
        return _capabilities(self.kind)

    def submit(self, request: WorkRequest, *, context: WorkerContext) -> ExecutionRecord:
        experiment = request.experiment
        tenant_id = experiment.tenant_id
        execution_id = _new_execution_id(tenant_id)
        now = _utcnow()
        reasons = (
            f"{self.kind.value} worker is a stub",
            "same Experiment type; no Docker/Kubernetes/queue SDK imported",
        )
        record = ExecutionRecord(
            id=execution_id,
            tenant_id=tenant_id,
            experiment_id=experiment.id,
            worker_kind=self.kind,
            status=ExecutionStatus.UNAVAILABLE,
            decision=PolicyDecision.SANDBOX_ONLY,
            reasons=reasons,
            trial_slots=resolve_trial_count(
                experiment, population_target=request.population_target
            ),
            concurrency=experiment.execution_budget.max_concurrency or 1,
            result={"available": False, "stub": True},
            created_at=now,
            updated_at=now,
        )
        _publish(
            context,
            tenant_id,
            EventKind.WORKER_UNAVAILABLE,
            execution_id=execution_id,
            experiment_id=experiment.id,
            payload={"worker_kind": self.kind.value},
        )
        return context.store.put_execution(record)

    def require(self) -> None:
        raise WorkerNotAvailableError(self.kind.value)


def get_worker(kind: WorkerKind | str | None = None) -> Worker:
    resolved = parse_worker_kind(kind)
    if resolved is WorkerKind.LOCAL:
        return LocalSandboxWorker()
    return RemoteWorkerStub(resolved)


class DataPlane:
    """Tenant-scoped artifact and event writes. No cloud vendor fields."""

    name = PlaneName.DATA

    def __init__(self, store: EnterpriseRepository) -> None:
        self.store = store

    def put_artifact(self, artifact: Artifact) -> Artifact:
        return self.store.put_artifact(artifact)

    def get_artifact(self, tenant_id: TenantId, artifact_id: ArtifactId) -> Artifact:
        return self.store.get_artifact(tenant_id, artifact_id)

    def list_artifacts(
        self,
        tenant_id: TenantId,
        *,
        execution_id: ExecutionId | None = None,
    ) -> list[Artifact]:
        return self.store.list_artifacts(tenant_id, execution_id=execution_id)

    def list_events(
        self,
        tenant_id: TenantId,
        *,
        execution_id: ExecutionId | None = None,
    ) -> list[EnterpriseEvent]:
        return self.store.list_events(tenant_id, execution_id=execution_id)


class ExecutionPlane:
    """Dispatch WorkRequest to a worker. Does not own IAM or billing."""

    name = PlaneName.EXECUTION

    def __init__(
        self,
        store: EnterpriseRepository,
        *,
        bus: EventBus | None = None,
        clock: SimulationClock | None = None,
        world: WorldState | None = None,
    ) -> None:
        self.store = store
        self.bus = bus or EventBus()
        self.clock = clock or SimulationClock()
        self.world = world or WorldState()

    def context(self, tenant_id: TenantId) -> WorkerContext:
        try:
            policy = self.store.get_model_policy(tenant_id)
        except EntityNotFoundError:
            policy = None
        return WorkerContext(
            store=self.store,
            bus=self.bus,
            clock=self.clock,
            world=self.world,
            policy=policy,
        )

    def submit(self, request: WorkRequest) -> ExecutionRecord:
        worker = get_worker(request.worker_kind)
        return worker.submit(request, context=self.context(request.experiment.tenant_id))

    def get(self, tenant_id: TenantId, execution_id: ExecutionId) -> ExecutionRecord:
        return self.store.get_execution(tenant_id, execution_id)

    def list(self, tenant_id: TenantId) -> list[ExecutionRecord]:
        return self.store.list_executions(tenant_id)


class ControlPlane:
    """Tenant-scoped launch commands. Does not import LiteLLM or Docker SDKs."""

    name = PlaneName.CONTROL

    def __init__(
        self,
        store: EnterpriseRepository,
        execution: ExecutionPlane,
        data: DataPlane | None = None,
    ) -> None:
        self.store = store
        self.execution = execution
        self.data = data or DataPlane(store)

    def submit_experiment(
        self,
        tenant_id: TenantId,
        experiment_id: ExperimentId,
        *,
        worker_kind: WorkerKind | str | None = None,
        approved: bool = False,
        enable_world_state: bool = False,
        model_provider: str | None = None,
    ) -> ExecutionRecord:
        experiment = self.store.get_experiment(tenant_id, experiment_id)
        paths = [
            f"legacy:{persona.legacy_persona_id}"
            for persona in self.store.list_personas(tenant_id)
            if persona.population_id
            and persona.population_id.value
            in {item.value for item in experiment.population_ids}
        ]
        population_target = None
        if experiment.population_ids:
            try:
                population_target = self.store.get_population(
                    tenant_id, experiment.population_ids[0]
                ).target_size
            except EntityNotFoundError:
                population_target = None
        request = WorkRequest(
            experiment=experiment,
            worker_kind=parse_worker_kind(worker_kind),
            persona_paths=tuple(paths),
            approved=approved,
            enable_world_state=enable_world_state,
            model_provider=model_provider,
            population_target=population_target,
        )
        return self.execution.submit(request)


@dataclass
class EnterpriseRuntime:
    """Thin wiring of the three planes for API and tests."""

    store: EnterpriseRepository
    bus: EventBus = field(default_factory=EventBus)
    clock: SimulationClock = field(default_factory=SimulationClock)
    world: WorldState = field(default_factory=WorldState)

    def __post_init__(self) -> None:
        self.data = DataPlane(self.store)
        self.execution = ExecutionPlane(
            self.store, bus=self.bus, clock=self.clock, world=self.world
        )
        self.control = ControlPlane(self.store, self.execution, self.data)

    def execute_experiment(
        self,
        tenant_id: TenantId,
        experiment_id: ExperimentId,
        **kwargs: Any,
    ) -> ExecutionRecord:
        return self.control.submit_experiment(tenant_id, experiment_id, **kwargs)
