"""Traces, hierarchical metrics, failure taxonomy, and evaluation SDK."""

from __future__ import annotations

from matraix.enterprise import (
    EvaluationResult,
    EvaluatorKind,
    FAILURE_CLASSES,
    FailureClass,
    InMemoryEnterpriseStore,
    MetricLevel,
    MetricPoint,
    MetricsRegistry,
    PolicyDecision,
    SYNTHETIC_METRIC_LIMITATION,
    WorkerKind,
    WorkRequest,
    classify_failure,
    create_tenant_with_default_org,
    evaluate_execution,
    supplemental_llm_judge,
)
from matraix.enterprise.ids import EntityKind, ExperimentId, new_id
from matraix.enterprise.runtime import EnterpriseRuntime
from matraix.enterprise.telemetry import common_attributes, new_trace_id


def test_observability_modules_do_not_import_sdks() -> None:
    import matraix.enterprise.evaluation as evaluation
    import matraix.enterprise.metrics as metrics
    import matraix.enterprise.observability as observability
    import matraix.enterprise.telemetry as telemetry

    for module in (evaluation, metrics, observability, telemetry):
        assert "litellm" not in module.__dict__
        assert "opentelemetry" not in module.__dict__
        assert "harbor" not in module.__dict__


def test_failure_taxonomy_is_complete() -> None:
    assert {item.value for item in FAILURE_CLASSES} == {
        "PERCEPTION_FAILURE",
        "INSTRUCTION_FAILURE",
        "REASONING_FAILURE",
        "TOOL_FAILURE",
        "KNOWLEDGE_FAILURE",
        "POLICY_FAILURE",
        "EXECUTION_FAILURE",
        "VERIFICATION_FAILURE",
        "INTERACTION_FAILURE",
        "LATENCY_FAILURE",
        "ESCALATION_FAILURE",
        "ENVIRONMENT_FAILURE",
    }
    assert classify_failure("policy denied RESTRICTED") is FailureClass.POLICY_FAILURE
    assert classify_failure("worker unavailable kubernetes stub") is (
        FailureClass.ENVIRONMENT_FAILURE
    )
    assert classify_failure("held for approval") is FailureClass.ESCALATION_FAILURE
    assert classify_failure("ocr misread") is FailureClass.PERCEPTION_FAILURE
    assert classify_failure("prompt instruction missed") is FailureClass.INSTRUCTION_FAILURE
    assert classify_failure("logic error in reason") is FailureClass.REASONING_FAILURE
    assert classify_failure("tool function_call failed") is FailureClass.TOOL_FAILURE
    assert classify_failure("hallucinated knowledge") is FailureClass.KNOWLEDGE_FAILURE
    assert classify_failure("assert reward mismatch") is FailureClass.VERIFICATION_FAILURE
    assert classify_failure("dialog turn failed") is FailureClass.INTERACTION_FAILURE
    assert classify_failure("timeout latency") is FailureClass.LATENCY_FAILURE
    assert classify_failure("unknown boom") is FailureClass.EXECUTION_FAILURE


def test_metrics_hierarchy_and_confidence_interval() -> None:
    store = InMemoryEnterpriseStore()
    tenant, _ = create_tenant_with_default_org(store, name="Acme", slug="acme")
    registry = MetricsRegistry()
    for level in MetricLevel:
        registry.record(
            MetricPoint(
                name="sandbox_units",
                value=2.0 if level is MetricLevel.STEP else 1.0,
                level=level,
                tenant_id=tenant.id,
                segments={"policy": "SANDBOX_ONLY"},
            )
        )
    registry.record(
        MetricPoint(
            name="sandbox_units",
            value=4.0,
            level=MetricLevel.STEP,
            tenant_id=tenant.id,
            segments={"policy": "SANDBOX_ONLY"},
        )
    )
    assert registry.hierarchy("sandbox_units", tenant_id=tenant.id) == [
        item.value for item in MetricLevel
    ]
    step = registry.aggregate(
        "sandbox_units", tenant_id=tenant.id, level=MetricLevel.STEP
    )[0]
    assert step.n == 2
    assert step.ci_low is not None and step.ci_high is not None
    assert step.limitation == SYNTHETIC_METRIC_LIMITATION
    enterprise = registry.aggregate(
        "sandbox_units", tenant_id=tenant.id, level=MetricLevel.ENTERPRISE
    )[0]
    assert enterprise.n == 1
    assert enterprise.ci_low is None


def test_deterministic_eval_beats_llm_judge() -> None:
    store = InMemoryEnterpriseStore()
    tenant, _ = create_tenant_with_default_org(store, name="Acme", slug="acme")
    experiment_id = ExperimentId(tenant.id, new_id(EntityKind.EXPERIMENT))
    failed = evaluate_execution(
        tenant_id=tenant.id,
        execution_id=None,
        experiment_id=experiment_id,
        result={"replaced_harbor_job": True, "sandbox": True},
        artifacts=[],
        status="completed",
        include_llm_judge=True,
    )
    assert failed.passed is False
    kinds = [item.kind for item in failed.results]
    assert EvaluatorKind.DETERMINISTIC in kinds
    assert EvaluatorKind.LLM_JUDGE in kinds
    judge = next(item for item in failed.results if item.kind is EvaluatorKind.LLM_JUDGE)
    assert judge.used_for_pass_fail is False
    assert judge.judge_supplemental is True
    assert failed.to_dict()["synthetic_equivalent_to_human_research"] is False
    assert any("not equivalent to human" in note for note in failed.notes)
    lone = supplemental_llm_judge(invoked=True)
    assert lone.used_for_pass_fail is False
    assert isinstance(lone, EvaluationResult)


def test_local_execution_writes_otel_trace_and_eval() -> None:
    store = InMemoryEnterpriseStore()
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    from matraix.enterprise import Experiment

    experiment = store.put_experiment(
        Experiment(
            id=ExperimentId(tenant.id, new_id(EntityKind.EXPERIMENT)),
            tenant_id=tenant.id,
            organization_id=org.id,
            hypothesis="H",
            objective="O",
            random_seed=42,
            task_path="application/tasks/example-survey_product-feedback",
            sample_size=2,
        )
    )
    record = EnterpriseRuntime(store).execute_experiment(tenant.id, experiment.id)
    assert record.decision is PolicyDecision.SANDBOX_ONLY
    assert record.result["trace_id"]
    assert len(record.result["trace_id"]) == 32
    assert record.result["evaluation"]["passed"] is True
    assert record.result["evaluation"]["synthetic_equivalent_to_human_research"] is False
    assert record.result["evaluation"]["recommended_human_validation"] is True
    assert record.result["failure"] is None
    artifacts = {item.kind: item for item in store.list_artifacts(tenant.id, execution_id=record.id)}
    assert {"trace", "metrics", "evaluation", "completion"} <= set(artifacts)
    trace = artifacts["trace"].content
    assert trace["trace_id"] == record.result["trace_id"]
    spans = trace["resource_spans"][0]["scope_spans"][0]["spans"]
    names = {span["name"] for span in spans}
    assert "enterprise.execute" in names
    assert "enterprise.policy" in names
    attrs = spans[0]["attributes"]
    assert attrs["tenant_id"] == tenant.id.value
    assert attrs["experiment_id"] == experiment.id.value
    assert "tokens" in attrs
    assert "policy.decision" in attrs
    metrics = artifacts["metrics"].content
    assert metrics["hierarchy"][0] == "step"
    assert metrics["hierarchy"][-1] == "enterprise"
    assert metrics["limitation"] == SYNTHETIC_METRIC_LIMITATION
    evaluation = artifacts["evaluation"].content
    assert evaluation["passed"] is True
    assert evaluation["synthetic_equivalent_to_human_research"] is False


def test_denied_execution_is_policy_failure() -> None:
    from matraix.enterprise import Experiment, ModelPolicy

    store = InMemoryEnterpriseStore()
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    store.put_model_policy(
        ModelPolicy(tenant_id=tenant.id, denied_actions=("execute_experiment",))
    )
    experiment = store.put_experiment(
        Experiment(
            id=ExperimentId(tenant.id, new_id(EntityKind.EXPERIMENT)),
            tenant_id=tenant.id,
            organization_id=org.id,
            hypothesis="H",
            objective="O",
        )
    )
    record = EnterpriseRuntime(store).execute_experiment(tenant.id, experiment.id)
    assert record.result["failure"] == "POLICY_FAILURE"
    assert record.result["evaluation"]["passed"] is False
    failure = next(
        item
        for item in store.list_artifacts(tenant.id, execution_id=record.id)
        if item.kind == "failure"
    )
    assert failure.content["classification"] == "POLICY_FAILURE"


def test_remote_stub_is_environment_failure() -> None:
    from matraix.enterprise import Experiment

    store = InMemoryEnterpriseStore()
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    experiment = store.put_experiment(
        Experiment(
            id=ExperimentId(tenant.id, new_id(EntityKind.EXPERIMENT)),
            tenant_id=tenant.id,
            organization_id=org.id,
            hypothesis="H",
            objective="O",
        )
    )
    record = EnterpriseRuntime(store).execution.submit(
        WorkRequest(experiment=experiment, worker_kind=WorkerKind.DOCKER)
    )
    assert record.result["failure"] == "ENVIRONMENT_FAILURE"
    assert record.result["evaluation"]["passed"] is False


def test_metrics_are_tenant_scoped() -> None:
    store = InMemoryEnterpriseStore()
    alpha, _ = create_tenant_with_default_org(store, name="Alpha", slug="alpha")
    bravo, _ = create_tenant_with_default_org(store, name="Bravo", slug="bravo")
    registry = MetricsRegistry()
    registry.record(
        MetricPoint(
            name="sandbox_units",
            value=1.0,
            level=MetricLevel.STEP,
            tenant_id=alpha.id,
        )
    )
    assert registry.points(tenant_id=bravo.id) == []
    assert registry.hierarchy("sandbox_units", tenant_id=bravo.id) == []


def test_trace_attributes_are_otel_shaped() -> None:
    tenant_id = create_tenant_with_default_org(
        InMemoryEnterpriseStore(), name="Acme", slug="acme"
    )[0].id
    attrs = common_attributes(
        tenant_id=tenant_id,
        task="application/tasks/example-survey_product-feedback",
        model="sandbox",
        seed=42,
        tokens=10,
        cost_usd=0.0,
        tool_calls=0,
        policy_decision="SANDBOX_ONLY",
        verification_result="pass",
    )
    assert set(attrs) >= {
        "tenant_id",
        "experiment_id",
        "execution_id",
        "persona_id",
        "task",
        "model",
        "seed",
        "code_version",
        "tokens",
        "cost_usd",
        "tool_calls",
        "policy.decision",
        "verification.result",
    }
    assert len(new_trace_id()) == 32
