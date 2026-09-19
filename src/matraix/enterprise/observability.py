"""Build trace, metric, evaluation, and failure snapshots for an execution."""

from __future__ import annotations

from typing import Any

from matraix.enterprise.evaluation import EvaluationBundle, evaluate_execution
from matraix.enterprise.ids import ExecutionId, ExperimentId, TenantId
from matraix.enterprise.metrics import MetricLevel, MetricPoint, MetricsRegistry
from matraix.enterprise.telemetry import (
    InMemoryTracer,
    SpanEvent,
    SpanStatus,
    common_attributes,
    resolve_code_version,
)


def record_execution_observability(
    *,
    tenant_id: TenantId,
    experiment_id: ExperimentId,
    execution_id: ExecutionId,
    status: str,
    decision: str,
    reasons: tuple[str, ...],
    result: dict[str, Any],
    artifacts: list[dict[str, Any]],
    task: str | None,
    model: str | None,
    seed: int | None,
    tokens: int,
    cost_usd: float,
    tool_calls: int,
    trial_slots: int,
    latency_ms: float,
    include_llm_judge: bool = False,
) -> dict[str, Any]:
    """Return persistable snapshots. Does not import Harbor or LiteLLM."""
    tracer = InMemoryTracer()
    metrics = MetricsRegistry()
    bundle = evaluate_execution(
        tenant_id=tenant_id,
        execution_id=execution_id,
        experiment_id=experiment_id,
        result=result,
        artifacts=artifacts,
        status=status,
        reasons=reasons,
        seed=seed,
        include_llm_judge=include_llm_judge,
    )
    verification = "pass" if bundle.passed else "fail"
    attrs = common_attributes(
        tenant_id=tenant_id,
        experiment_id=experiment_id,
        execution_id=execution_id,
        task=task,
        model=model,
        seed=seed,
        tokens=tokens,
        cost_usd=cost_usd,
        tool_calls=tool_calls,
        policy_decision=decision,
        verification_result=verification,
    )
    root = tracer.start_span("enterprise.execute", attributes=attrs)
    policy_span = tracer.start_span(
        "enterprise.policy",
        parent=root,
        attributes={**attrs, "policy.decision": decision},
    )
    policy_span.events.append(
        SpanEvent(name="policy.evaluated", attributes={"decision": decision})
    )
    policy_span.end()
    complete_span = tracer.start_span(
        "enterprise.complete",
        parent=root,
        attributes={**attrs, "tokens": tokens, "cost_usd": cost_usd},
    )
    complete_span.end()
    eval_span = tracer.start_span(
        "enterprise.evaluate",
        parent=root,
        attributes={**attrs, "verification.result": verification},
    )
    eval_span.end(status=SpanStatus.OK if bundle.passed else SpanStatus.ERROR)
    root.attributes["latency_ms"] = latency_ms
    root.end(status=SpanStatus.OK if status == "completed" else SpanStatus.ERROR)

    provenance = {
        "source": "enterprise.sandbox",
        "seed": seed,
        "code_version": resolve_code_version(),
        "execution_id": execution_id.value,
        "experiment_id": experiment_id.value,
    }
    segments = {"policy": decision, "status": status, "worker": "local"}
    values = {
        MetricLevel.STEP: float(tokens),
        MetricLevel.TASK: float(trial_slots),
        MetricLevel.SESSION: 1.0,
        MetricLevel.PERSONA: 1.0,
        MetricLevel.COHORT: 1.0,
        MetricLevel.POPULATION: float(max(1, trial_slots)),
        MetricLevel.EXPERIMENT: 1.0 if bundle.passed else 0.0,
        MetricLevel.ENTERPRISE: 1.0,
    }
    for level, value in values.items():
        metrics.record(
            MetricPoint(
                name="sandbox_units",
                value=value,
                level=level,
                tenant_id=tenant_id,
                unit="count",
                segments=segments,
                provenance=provenance,
                execution_id=execution_id,
                experiment_id=experiment_id,
            )
        )
    metrics.record(
        MetricPoint(
            name="latency_ms",
            value=latency_ms,
            level=MetricLevel.STEP,
            tenant_id=tenant_id,
            unit="ms",
            segments=segments,
            provenance=provenance,
            execution_id=execution_id,
            experiment_id=experiment_id,
        )
    )
    metrics.record(
        MetricPoint(
            name="tokens",
            value=float(tokens),
            level=MetricLevel.STEP,
            tenant_id=tenant_id,
            unit="tokens",
            segments=segments,
            provenance=provenance,
            execution_id=execution_id,
            experiment_id=experiment_id,
        )
    )
    metrics.record(
        MetricPoint(
            name="cost_usd",
            value=float(cost_usd),
            level=MetricLevel.EXPERIMENT,
            tenant_id=tenant_id,
            unit="usd",
            segments=segments,
            provenance=provenance,
            execution_id=execution_id,
            experiment_id=experiment_id,
        )
    )
    return {
        "trace": tracer.export(),
        "metrics": {
            **metrics.to_dict(tenant_id=tenant_id),
            "hierarchy": metrics.hierarchy("sandbox_units", tenant_id=tenant_id),
            "aggregates": [
                item.to_dict()
                for item in metrics.aggregate("sandbox_units", tenant_id=tenant_id)
            ],
        },
        "evaluation": bundle.to_dict(),
        "failure": bundle.failure.to_dict() if bundle.failure else None,
        "trace_id": tracer.trace_id,
        "bundle": bundle,
    }


def evaluation_from_stored(
    *,
    tenant_id: TenantId,
    execution_id: ExecutionId,
    experiment_id: ExperimentId,
    result: dict[str, Any],
    artifacts: list[dict[str, Any]],
    status: str,
    reasons: tuple[str, ...],
    seed: int | None,
    include_llm_judge: bool = False,
) -> EvaluationBundle:
    return evaluate_execution(
        tenant_id=tenant_id,
        execution_id=execution_id,
        experiment_id=experiment_id,
        result=result,
        artifacts=artifacts,
        status=status,
        reasons=reasons,
        seed=seed,
        include_llm_judge=include_llm_judge,
    )
