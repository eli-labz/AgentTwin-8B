"""Enterprise-admin journey: tenant → population → experiment → report → audit → re-run → isolation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from matraix.enterprise.api import create_enterprise_app
from matraix.enterprise.repositories import InMemoryEnterpriseStore
from matraix.enterprise.reporting import REQUIRED_LIMITATIONS


def test_admin_journey_real_steps_and_tenant_isolation() -> None:
    client = TestClient(create_enterprise_app(InMemoryEnterpriseStore()))

    alpha = client.post("/api/v1/tenants", json={"name": "Alpha", "slug": "alpha-journey"})
    bravo = client.post("/api/v1/tenants", json={"name": "Bravo", "slug": "bravo-journey"})
    assert alpha.status_code == 200
    assert bravo.status_code == 200
    tenant_a = alpha.json()["id"]
    tenant_b = bravo.json()["id"]
    headers_a = {"X-Tenant-Id": tenant_a}
    headers_b = {"X-Tenant-Id": tenant_b}

    population = client.post(
        "/api/v1/populations",
        json={"name": "Workforce", "target_size": 10},
        headers=headers_a,
    )
    assert population.status_code == 200
    assert population.json()["tenant_id"] == tenant_a

    experiment = client.post(
        "/api/v1/experiments",
        json={
            "hypothesis": "Novice users retry more often",
            "objective": "Measure retry rate",
            "random_seed": 42,
            "task_path": "application/tasks/example-survey_product-feedback",
            "sample_size": 2,
        },
        headers=headers_a,
    )
    assert experiment.status_code == 200
    experiment_id = experiment.json()["id"]
    assert experiment.json()["random_seed"] == 42

    first = client.post(
        f"/api/v1/experiments/{experiment_id}/execute",
        json={"worker_kind": "local"},
        headers=headers_a,
    )
    assert first.status_code == 200
    first_id = first.json()["id"]
    assert first.json()["status"] in {"completed", "COMPLETED"} or first.json().get(
        "decision"
    ) in {"sandbox_only", "SANDBOX_ONLY"}

    report = client.get(
        f"/api/v1/experiments/{experiment_id}/report",
        headers=headers_a,
    )
    assert report.status_code == 200
    body = report.json()
    assert body["synthetic_equivalent_to_human_research"] is False
    assert body["recommended_human_validation"] is True
    for note in REQUIRED_LIMITATIONS:
        assert note in body["limitations"]

    audit = client.get("/api/v1/audit/export", headers=headers_a)
    assert audit.status_code == 200
    assert audit.json()["schema_version"] == "EnterpriseAuditExport.v1"
    assert audit.json()["tenant_id"] == tenant_a

    rerun = client.post(
        f"/api/v1/experiments/{experiment_id}/execute",
        json={"worker_kind": "local"},
        headers=headers_a,
    )
    assert rerun.status_code == 200
    assert rerun.json()["id"] != first_id
    seed_again = client.get(
        f"/api/v1/experiments/{experiment_id}",
        headers=headers_a,
    )
    assert seed_again.json()["random_seed"] == 42

    hidden_report = client.get(
        f"/api/v1/experiments/{experiment_id}/report",
        headers=headers_b,
    )
    assert hidden_report.status_code in {403, 404}
    hidden_exec = client.get(
        f"/api/v1/executions/{first_id}",
        headers=headers_b,
    )
    assert hidden_exec.status_code in {403, 404}
    bravo_audit = client.get("/api/v1/audit/export", headers=headers_b)
    assert bravo_audit.status_code == 200
    assert bravo_audit.json()["tenant_id"] == tenant_b
    assert first_id not in str(bravo_audit.json())

    workers = client.get("/api/v1/workers").json()
    kinds = {item["kind"]: item for item in workers}
    assert kinds["local"]["available"] is True
    for stub in ("docker", "kubernetes", "queue", "batch"):
        assert kinds[stub]["available"] is False

    oidc = client.get("/api/v1/auth/oidc").json()
    assert oidc.get("live_idp_required") is False
