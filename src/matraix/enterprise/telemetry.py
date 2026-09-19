"""OpenTelemetry-compatible traces and the enterprise failure taxonomy.

No OpenTelemetry SDK is imported. Span JSON uses the same field names an
exporter would (`trace_id`, `span_id`, attributes). Tenant isolation is
enforced by the data plane, not by a vendor backend.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from matraix.enterprise.ids import ExecutionId, ExperimentId, PersonaId, TenantId


def new_trace_id() -> str:
    return uuid.uuid4().hex


def new_span_id() -> str:
    return uuid.uuid4().hex[:16]


def resolve_code_version() -> str:
    try:
        from importlib.metadata import version

        return version("matraix")
    except Exception:
        return "0.1.0"


class SpanStatus(str, Enum):
    OK = "OK"
    ERROR = "ERROR"


class FailureClass(str, Enum):
    PERCEPTION_FAILURE = "PERCEPTION_FAILURE"
    INSTRUCTION_FAILURE = "INSTRUCTION_FAILURE"
    REASONING_FAILURE = "REASONING_FAILURE"
    TOOL_FAILURE = "TOOL_FAILURE"
    KNOWLEDGE_FAILURE = "KNOWLEDGE_FAILURE"
    POLICY_FAILURE = "POLICY_FAILURE"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    VERIFICATION_FAILURE = "VERIFICATION_FAILURE"
    INTERACTION_FAILURE = "INTERACTION_FAILURE"
    LATENCY_FAILURE = "LATENCY_FAILURE"
    ESCALATION_FAILURE = "ESCALATION_FAILURE"
    ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"


FAILURE_CLASSES: tuple[FailureClass, ...] = tuple(FailureClass)


@dataclass
class SpanEvent:
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    timestamp_unix_nano: int = field(default_factory=lambda: time.time_ns())

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "attributes": dict(self.attributes),
            "timestamp_unix_nano": self.timestamp_unix_nano,
        }


@dataclass
class SpanRecord:
    trace_id: str
    span_id: str
    name: str
    parent_span_id: str | None = None
    start_time_unix_nano: int = field(default_factory=lambda: time.time_ns())
    end_time_unix_nano: int | None = None
    status: SpanStatus = SpanStatus.OK
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[SpanEvent] = field(default_factory=list)

    def end(self, *, status: SpanStatus | None = None) -> None:
        self.end_time_unix_nano = time.time_ns()
        if status is not None:
            self.status = status

    @property
    def latency_ms(self) -> float:
        if self.end_time_unix_nano is None:
            return 0.0
        return max(0.0, (self.end_time_unix_nano - self.start_time_unix_nano) / 1_000_000)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "start_time_unix_nano": self.start_time_unix_nano,
            "end_time_unix_nano": self.end_time_unix_nano,
            "status": {"code": self.status.value},
            "attributes": dict(self.attributes),
            "events": [item.to_dict() for item in self.events],
            "latency_ms": self.latency_ms,
        }


@dataclass
class FailureRecord:
    classification: FailureClass
    message: str
    tenant_id: TenantId
    execution_id: ExecutionId | None = None
    experiment_id: ExperimentId | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "message": self.message,
            "tenant_id": str(self.tenant_id),
            "execution_id": self.execution_id.value if self.execution_id else None,
            "experiment_id": self.experiment_id.value if self.experiment_id else None,
            "details": dict(self.details),
        }


def classify_failure(text: str) -> FailureClass:
    """Map a reason string onto the taxonomy. Unknown text stays EXECUTION_FAILURE."""
    lowered = (text or "").strip().lower()
    rules = (
        (("policy", "deny", "denied", "allow-list", "restricted"), FailureClass.POLICY_FAILURE),
        (("timeout", "latency", "slow"), FailureClass.LATENCY_FAILURE),
        (("verif", "assert", "reward"), FailureClass.VERIFICATION_FAILURE),
        (("tool", "function_call"), FailureClass.TOOL_FAILURE),
        (("instruction", "prompt"), FailureClass.INSTRUCTION_FAILURE),
        (("perception", "ocr", "misread"), FailureClass.PERCEPTION_FAILURE),
        (("reason", "logic"), FailureClass.REASONING_FAILURE),
        (("knowledge", "hallucin"), FailureClass.KNOWLEDGE_FAILURE),
        (("escalat", "approval", "held"), FailureClass.ESCALATION_FAILURE),
        (("interact", "dialog", "turn"), FailureClass.INTERACTION_FAILURE),
        (("environment", "unavailable", "docker", "kubernetes", "stub"), FailureClass.ENVIRONMENT_FAILURE),
    )
    for needles, classification in rules:
        if any(needle in lowered for needle in needles):
            return classification
    return FailureClass.EXECUTION_FAILURE


class InMemoryTracer:
    """Process-local span collector with OTel-shaped export."""

    def __init__(self, *, trace_id: str | None = None) -> None:
        self.trace_id = trace_id or new_trace_id()
        self._spans: list[SpanRecord] = []

    def start_span(
        self,
        name: str,
        *,
        parent: SpanRecord | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> SpanRecord:
        span = SpanRecord(
            trace_id=self.trace_id,
            span_id=new_span_id(),
            name=name,
            parent_span_id=parent.span_id if parent else None,
            attributes=dict(attributes or {}),
        )
        self._spans.append(span)
        return span

    def export(self) -> dict[str, Any]:
        return {
            "resource_spans": [
                {
                    "resource": {
                        "attributes": {
                            "service.name": "matraix.enterprise",
                            "service.version": resolve_code_version(),
                        }
                    },
                    "scope_spans": [
                        {
                            "scope": {"name": "matraix.enterprise.telemetry"},
                            "spans": [span.to_dict() for span in self._spans],
                        }
                    ],
                }
            ],
            "trace_id": self.trace_id,
        }


def common_attributes(
    *,
    tenant_id: TenantId,
    experiment_id: ExperimentId | None = None,
    execution_id: ExecutionId | None = None,
    persona_id: PersonaId | None = None,
    task: str | None = None,
    model: str | None = None,
    seed: int | None = None,
    tokens: int | None = None,
    cost_usd: float | None = None,
    tool_calls: int | None = None,
    policy_decision: str | None = None,
    verification_result: str | None = None,
) -> dict[str, Any]:
    return {
        "tenant_id": str(tenant_id),
        "experiment_id": experiment_id.value if experiment_id else None,
        "execution_id": execution_id.value if execution_id else None,
        "persona_id": persona_id.value if persona_id else None,
        "task": task,
        "model": model,
        "seed": seed,
        "code_version": resolve_code_version(),
        "tokens": tokens,
        "cost_usd": cost_usd,
        "tool_calls": tool_calls,
        "policy.decision": policy_decision,
        "verification.result": verification_result,
    }
