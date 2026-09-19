"""Executive reports from Phase 6 artifacts — limitations always present."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from matraix.enterprise import (
    EXECUTIVE_REPORT_SCHEMA,
    Experiment,
    InMemoryEnterpriseStore,
    REQUIRED_LIMITATIONS,
    WorkerKind,
    WorkRequest,
    build_executive_report,
    create_tenant_with_default_org,
    format_report_csv,
    format_report_html,
    format_report_json,
    render_report,
    report_from_runtime,
)
from matraix.enterprise.api import create_enterprise_app
from matraix.enterprise.evaluation import HUMAN_VALIDATION_NOTE
from matraix.enterprise.ids import EntityKind, ExecutionId, ExperimentId, new_id
from matraix.enterprise.reporting import SCHEMA_VERSION, ExecutiveReport
from matraix.enterprise.runtime import (
    EnterpriseRuntime,
    ExecutionRecord,
    ExecutionStatus,
)


def test_reporting_module_does_not_import_sdks() -> None:
    import matraix.enterprise.reporting as reporting

    assert "litellm" not in reporting.__dict__
    assert "opentelemetry" not in reporting.__dict__
    assert "harbor" not in reporting.__dict__


def _record(
    tenant_id,
    experiment_id,
    *,
    status: str = "completed",
    decision: str = "SANDBOX_ONLY",
    worker: str = "local",
    passed: bool = True,
    failure: str | None = None,
    tokens: float = 10.0,
    cost: float = 0.02,
    latency: float = 12.0,
) -> tuple[ExecutionRecord, list[dict]]:
    execution_id = ExecutionId(tenant_id, new_id(EntityKind.EXECUTION))
    record = ExecutionRecord(
        id=execution_id,
        tenant_id=tenant_id,
        experiment_id=experiment_id,
        worker_kind=worker,
        status=status,
        decision=decision,
        result={
            "trace_id": "a" * 32,
            "seed": 42,
            "code_version": "test",
            "failure": failure,
        },
    )
    artifacts = [
        {
            "kind": "evaluation",
            "content": {
                "passed": passed,
                "synthetic_equivalent_to_human_research": False,
                "seed": 42,
                "code_version": "test",
            },
        },
        {
            "kind": "metrics",
            "content": {
                "points": [
                    {"name": "tokens", "value": tokens},
                    {"name": "cost_usd", "value": cost},
                    {"name": "latency_ms", "value": latency},
                ],
                "aggregates": [{"ci95": [0.1, 0.9]}],
            },
        },
    ]
    if failure:
        artifacts.append({"kind": "failure", "content": {"classification": failure}})
    return record, artifacts


def test_executive_report_forces_limitations_and_human_validation() -> None:
    store = InMemoryEnterpriseStore()
    tenant, _ = create_tenant_with_default_org(store, name="Acme", slug="acme")
    experiment_id = ExperimentId(tenant.id, new_id(EntityKind.EXPERIMENT))
    passed, passed_arts = _record(tenant.id, experiment_id, passed=True)
    failed, failed_arts = _record(
        tenant.id,
        experiment_id,
        status="denied",
        decision="DENY",
        passed=False,
        failure="POLICY_FAILURE",
        tokens=4,
        cost=0.0,
        latency=3,
    )
    report = build_executive_report(
        tenant_id=tenant.id,
        records=[passed, failed],
        artifacts_by_execution={
            passed.id.value: passed_arts,
            failed.id.value: failed_arts,
        },
        experiment_id=experiment_id,
    )
    payload = report.to_dict()
    assert payload["schema_version"] == SCHEMA_VERSION == EXECUTIVE_REPORT_SCHEMA
    assert payload["synthetic_equivalent_to_human_research"] is False
    assert payload["recommended_human_validation"] is True
    for note in REQUIRED_LIMITATIONS:
        assert note in payload["limitations"]
    assert HUMAN_VALIDATION_NOTE in payload["limitations"]
    assert payload["success"]["n"] == 2
    assert payload["success"]["passed"] == 1
    assert payload["success"]["failed"] == 1
    assert payload["success"]["rate"] == 0.5
    assert payload["risk"]["denied"] == 1
    assert payload["risk"]["failure_classes"]["POLICY_FAILURE"] == 1
    assert payload["cost"]["tokens"] == 14.0
    assert payload["confidence"]["ci95"] is not None
    assert len(payload["confidence"]["ci95"]) == 2
    segments = {(item["segment"], item["value"]) for item in payload["subgroups"]}
    assert ("status", "completed") in segments
    assert ("status", "denied") in segments
    assert ("policy", "SANDBOX_ONLY") in segments
    assert payload["reproducibility"]["seed"] == 42
    assert payload["reproducibility"]["code_version"] == "test"
    assert payload["drill_down"]

    stripped = ExecutiveReport(
        tenant_id=tenant.id,
        scope="execution",
        limitations=(),
        recommended_human_validation=False,
    )
    forced = stripped.to_dict()
    assert forced["recommended_human_validation"] is True
    assert forced["synthetic_equivalent_to_human_research"] is False
    for note in REQUIRED_LIMITATIONS:
        assert note in forced["limitations"]


def test_confidence_omitted_when_n_lt_2() -> None:
    store = InMemoryEnterpriseStore()
    tenant, _ = create_tenant_with_default_org(store, name="Acme", slug="acme")
    experiment_id = ExperimentId(tenant.id, new_id(EntityKind.EXPERIMENT))
    record, artifacts = _record(tenant.id, experiment_id)
    report = build_executive_report(
        tenant_id=tenant.id,
        records=[record],
        artifacts_by_execution={record.id.value: artifacts},
    )
    assert report.to_dict()["confidence"]["ci95"] is None
    assert report.to_dict()["success"]["n"] == 1


def test_report_formats_include_mandatory_copy() -> None:
    store = InMemoryEnterpriseStore()
    tenant, _ = create_tenant_with_default_org(store, name="Acme", slug="acme")
    experiment_id = ExperimentId(tenant.id, new_id(EntityKind.EXPERIMENT))
    record, artifacts = _record(tenant.id, experiment_id)
    report = build_executive_report(
        tenant_id=tenant.id,
        records=[record],
        artifacts_by_execution={record.id.value: artifacts},
        experiment_id=experiment_id,
    )
    payload = json.loads(format_report_json(report))
    assert payload["synthetic_equivalent_to_human_research"] is False
    assert payload["recommended_human_validation"] is True
    csv_text = format_report_csv(report)
    assert "synthetic_equivalent_to_human_research" in csv_text
    assert "false" in csv_text
    assert "recommended_human_validation" in csv_text
    assert HUMAN_VALIDATION_NOTE in csv_text
    html = format_report_html(report)
    assert "not human research" in html.lower()
    assert "synthetic_equivalent_to_human_research: false" in html
    assert "recommended_human_validation: true" in html
    assert "@page" in html
    body, media = render_report(report, "html")
    assert media.startswith("text/html")
    assert "Limitations" in body
    csv_body, csv_media = render_report(report, "csv")
    assert csv_media.startswith("text/csv")
    assert HUMAN_VALIDATION_NOTE in csv_body
    with pytest.raises(ValueError, match="unsupported"):
        render_report(report, "xlsx")


def test_report_from_runtime_is_tenant_scoped() -> None:
    store = InMemoryEnterpriseStore()
    alpha, org = create_tenant_with_default_org(store, name="Alpha", slug="alpha")
    bravo, _ = create_tenant_with_default_org(store, name="Bravo", slug="bravo")
    experiment = store.put_experiment(
        Experiment(
            id=ExperimentId(alpha.id, new_id(EntityKind.EXPERIMENT)),
            tenant_id=alpha.id,
            organization_id=org.id,
            hypothesis="H",
            objective="O",
            random_seed=42,
            task_path="application/tasks/example-survey_product-feedback",
            sample_size=2,
        )
    )
    runtime = EnterpriseRuntime(store)
    record = runtime.execute_experiment(alpha.id, experiment.id)
    report = report_from_runtime(
        runtime, tenant_id=alpha.id, execution_id=record.id
    )
    payload = report.to_dict()
    assert payload["tenant_id"] == alpha.id.value
    assert payload["synthetic_equivalent_to_human_research"] is False
    assert payload["success"]["n"] == 1
    assert payload["reproducibility"]["trace_ids"]

    experiment_report = report_from_runtime(
        runtime, tenant_id=alpha.id, experiment_id=experiment.id
    )
    assert experiment_report.to_dict()["scope"] == "experiment"
    assert record.id.value in experiment_report.execution_ids

    with pytest.raises(Exception):
        report_from_runtime(runtime, tenant_id=bravo.id, execution_id=record.id)
    with pytest.raises(Exception):
        report_from_runtime(
            runtime, tenant_id=bravo.id, experiment_id=experiment.id
        )


def test_experiment_report_rolls_up_unavailable_worker() -> None:
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
    runtime = EnterpriseRuntime(store)
    local = runtime.execute_experiment(tenant.id, experiment.id)
    remote = runtime.execution.submit(
        WorkRequest(experiment=experiment, worker_kind=WorkerKind.DOCKER)
    )
    report = report_from_runtime(
        runtime, tenant_id=tenant.id, experiment_id=experiment.id
    )
    payload = report.to_dict()
    assert payload["success"]["n"] == 2
    assert payload["risk"]["unavailable"] == 1
    assert local.id.value in payload["execution_ids"]
    assert remote.id.value in payload["execution_ids"]
    assert payload["recommended_human_validation"] is True
    assert payload["synthetic_equivalent_to_human_research"] is False
    assert any(item["status"] == ExecutionStatus.UNAVAILABLE.value for item in payload["drill_down"]) or any(
        item["status"] == "unavailable" for item in payload["drill_down"]
    )


def test_api_report_exports_and_isolation() -> None:
    client = TestClient(create_enterprise_app(InMemoryEnterpriseStore()))
    alpha = client.post("/api/v1/tenants", json={"name": "Alpha", "slug": "alpha"}).json()
    bravo = client.post("/api/v1/tenants", json={"name": "Bravo", "slug": "bravo"}).json()
    headers = {"X-Tenant-Id": alpha["id"]}
    created = client.post(
        "/api/v1/experiments",
        json={
            "hypothesis": "Novice users retry more often",
            "objective": "Measure retry rate",
            "random_seed": 42,
            "task_path": "application/tasks/example-survey_product-feedback",
            "sample_size": 2,
        },
        headers=headers,
    )
    experiment_id = created.json()["id"]
    executed = client.post(
        f"/api/v1/experiments/{experiment_id}/execute",
        json={"worker_kind": "local"},
        headers=headers,
    )
    execution_id = executed.json()["id"]

    spec = client.get("/openapi.json").json()
    assert "/api/v1/executions/{execution_id}/report" in spec["paths"]
    assert "/api/v1/experiments/{experiment_id}/report" in spec["paths"]

    json_body = client.get(
        f"/api/v1/executions/{execution_id}/report", headers=headers
    )
    assert json_body.status_code == 200
    payload = json_body.json()
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["synthetic_equivalent_to_human_research"] is False
    assert payload["recommended_human_validation"] is True
    assert payload["success"]["n"] == 1
    for note in REQUIRED_LIMITATIONS:
        assert note in payload["limitations"]

    csv_body = client.get(
        f"/api/v1/executions/{execution_id}/report?format=csv", headers=headers
    )
    assert csv_body.status_code == 200
    assert "text/csv" in csv_body.headers["content-type"]
    assert HUMAN_VALIDATION_NOTE in csv_body.text

    html_body = client.get(
        f"/api/v1/executions/{execution_id}/report?format=html", headers=headers
    )
    assert html_body.status_code == 200
    assert "text/html" in html_body.headers["content-type"]
    assert "not human research" in html_body.text.lower()

    experiment_report = client.get(
        f"/api/v1/experiments/{experiment_id}/report?format=json",
        headers=headers,
    )
    assert experiment_report.status_code == 200
    assert experiment_report.json()["scope"] == "experiment"

    bad = client.get(
        f"/api/v1/executions/{execution_id}/report?format=xlsx",
        headers=headers,
    )
    assert bad.status_code == 400

    hidden = client.get(
        f"/api/v1/executions/{execution_id}/report",
        headers={"X-Tenant-Id": bravo["id"]},
    )
    assert hidden.status_code == 404
    hidden_exp = client.get(
        f"/api/v1/experiments/{experiment_id}/report",
        headers={"X-Tenant-Id": bravo["id"]},
    )
    assert hidden_exp.status_code == 404
