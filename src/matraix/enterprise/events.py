"""Event-driven pieces additive to Harbor trials.

These types sit beside Harbor ``Job`` / ``Trial``. They do not replace the
simulation loop. Persistent memory stays off unless :class:`WorldState` is
explicitly enabled for a tenant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from matraix.enterprise.errors import EnterpriseSchemaError
from matraix.enterprise.ids import (
    EntityKind,
    EventId,
    ExecutionId,
    ExperimentId,
    TenantId,
    new_id,
)


class EventKind(str, Enum):
    EXECUTION_SUBMITTED = "execution_submitted"
    POLICY_EVALUATED = "policy_evaluated"
    WORKER_STARTED = "worker_started"
    TRIAL_SCHEDULED = "trial_scheduled"
    ARTIFACT_WRITTEN = "artifact_written"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_DENIED = "execution_denied"
    EXECUTION_HELD = "execution_held"
    WORKER_UNAVAILABLE = "worker_unavailable"
    CLOCK_TICK = "clock_tick"


@dataclass(frozen=True, slots=True)
class EnterpriseEvent:
    id: EventId
    tenant_id: TenantId
    kind: EventKind
    payload: dict[str, Any] = field(default_factory=dict)
    experiment_id: ExperimentId | None = None
    execution_id: ExecutionId | None = None
    tick: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("event id tenant mismatch")
        if self.experiment_id is not None and self.experiment_id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("event experiment is not in this tenant")
        if self.execution_id is not None and self.execution_id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("event execution is not in this tenant")
        kind = self.kind if isinstance(self.kind, EventKind) else EventKind(self.kind)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "tick", int(self.tick))
        object.__setattr__(self, "payload", dict(self.payload or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id.value,
            "tenant_id": self.tenant_id.value,
            "kind": self.kind.value,
            "payload": dict(self.payload),
            "experiment_id": self.experiment_id.value if self.experiment_id else None,
            "execution_id": self.execution_id.value if self.execution_id else None,
            "tick": self.tick,
            "created_at": self.created_at.isoformat(),
        }


def event_from_dict(payload: dict[str, Any]) -> EnterpriseEvent:
    tenant = TenantId(payload["tenant_id"])
    experiment = (
        ExperimentId(tenant, payload["experiment_id"])
        if payload.get("experiment_id")
        else None
    )
    execution = (
        ExecutionId(tenant, payload["execution_id"])
        if payload.get("execution_id")
        else None
    )
    created = payload.get("created_at")
    return EnterpriseEvent(
        id=EventId(tenant, payload["id"]),
        tenant_id=tenant,
        kind=payload["kind"],
        payload=dict(payload.get("payload") or {}),
        experiment_id=experiment,
        execution_id=execution,
        tick=int(payload.get("tick") or 0),
        created_at=datetime.fromisoformat(created) if created else datetime.now(timezone.utc),
    )


def new_event(
    tenant_id: TenantId,
    kind: EventKind | str,
    *,
    payload: dict[str, Any] | None = None,
    experiment_id: ExperimentId | None = None,
    execution_id: ExecutionId | None = None,
    tick: int = 0,
) -> EnterpriseEvent:
    return EnterpriseEvent(
        id=EventId(tenant_id, new_id(EntityKind.EVENT)),
        tenant_id=tenant_id,
        kind=kind,
        payload=payload or {},
        experiment_id=experiment_id,
        execution_id=execution_id,
        tick=tick,
    )


class SimulationClock:
    """Logical clock for sandbox scheduling. Not a Harbor trial timer."""

    def __init__(self, start: int = 0) -> None:
        self._tick = int(start)

    @property
    def tick(self) -> int:
        return self._tick

    def now(self) -> int:
        return self._tick

    def advance(self, steps: int = 1) -> int:
        if int(steps) < 1:
            raise EnterpriseSchemaError("clock advance must be >= 1")
        self._tick += int(steps)
        return self._tick


class WorldState:
    """Optional in-process overlay. Disabled per tenant unless enabled.

    This is not Harbor environment state and is not persistent memory.
    """

    def __init__(self) -> None:
        self._enabled: set[str] = set()
        self._values: dict[str, dict[str, Any]] = {}

    def is_enabled(self, tenant_id: TenantId) -> bool:
        return tenant_id.value in self._enabled

    def enable(self, tenant_id: TenantId) -> None:
        self._enabled.add(tenant_id.value)
        self._values.setdefault(tenant_id.value, {})

    def get(self, tenant_id: TenantId, key: str, default: Any = None) -> Any:
        if not self.is_enabled(tenant_id):
            return default
        return self._values.get(tenant_id.value, {}).get(key, default)

    def set(self, tenant_id: TenantId, key: str, value: Any) -> None:
        if not self.is_enabled(tenant_id):
            raise EnterpriseSchemaError(
                "world state is disabled; enable explicitly (persistent memory stays off)"
            )
        self._values.setdefault(tenant_id.value, {})[str(key)] = value

    def snapshot(self, tenant_id: TenantId) -> dict[str, Any]:
        if not self.is_enabled(tenant_id):
            return {}
        return dict(self._values.get(tenant_id.value, {}))


class EventBus:
    """In-process pub/sub. Callers persist events through the data plane."""

    def __init__(self) -> None:
        self._events: list[EnterpriseEvent] = []
        self._handlers: list[Callable[[EnterpriseEvent], None]] = []

    def subscribe(self, handler: Callable[[EnterpriseEvent], None]) -> None:
        self._handlers.append(handler)

    def publish(self, event: EnterpriseEvent) -> EnterpriseEvent:
        self._events.append(event)
        for handler in self._handlers:
            handler(event)
        return event

    def history(
        self,
        tenant_id: TenantId | None = None,
        *,
        execution_id: ExecutionId | None = None,
    ) -> list[EnterpriseEvent]:
        items = list(self._events)
        if tenant_id is not None:
            items = [item for item in items if item.tenant_id == tenant_id]
        if execution_id is not None:
            items = [item for item in items if item.execution_id == execution_id]
        return items
