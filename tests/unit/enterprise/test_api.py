"""Enterprise /api/v1 authz and tenant isolation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from matraix.enterprise.api import create_enterprise_app
from matraix.enterprise.repositories import InMemoryEnterpriseStore


def _client(monkeypatch: pytest.MonkeyPatch | None = None) -> TestClient:
    if monkeypatch is not None:
        monkeypatch.delenv("MATRIX_ENTERPRISE_API_TOKEN", raising=False)
    return TestClient(create_enterprise_app(InMemoryEnterpriseStore()))


def _legacy_record() -> dict:
    return {
        "persona_id": "0042",
        "version": "1.0",
        "source": "synthetic",
        "display_name": "Casey Brooks",
        "dimensions": {"age_bracket": "25-34", "role_function": "Teaching"},
    }


def test_openapi_exposes_v1_paths() -> None:
    client = TestClient(create_enterprise_app(InMemoryEnterpriseStore()))
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    assert "/api/v1/tenants" in paths
    assert "/api/v1/populations" in paths
    assert "/api/v1/personas" in paths
    assert "/api/v1/org-edges" in paths
    assert "/api/v1/population-declarations" in paths
    assert "/api/v1/experiments" in paths
    assert "/api/v1/experiments/{experiment_id}/estimate" in paths
    assert "/api/v1/experiments/{experiment_id}/harbor-job" in paths
    assert "/api/v1/policy/evaluate" in paths
    assert "/api/v1/models/route" in paths
    assert "/api/v1/models/complete" in paths
    assert "/api/v1/models/catalog" in paths
    assert "/api/v1/model-policy" in paths
    assert spec["info"]["title"] == "AgentTwin Enterprise API"


def test_create_tenant_population_persona_round_trip() -> None:
    client = _client()
    created = client.post(
        "/api/v1/tenants", json={"name": "Acme Research", "slug": "acme"}
    )
    assert created.status_code == 200
    tenant = created.json()
    assert tenant["slug"] == "acme"
    assert tenant["default_organization_id"]
    tenant_id = tenant["id"]
    headers = {"X-Tenant-Id": tenant_id}

    population = client.post(
        "/api/v1/populations",
        json={"name": "Workforce", "target_size": 12},
        headers=headers,
    )
    assert population.status_code == 200
    assert population.json()["tenant_id"] == tenant_id
    population_id = population.json()["id"]

    persona = client.post(
        "/api/v1/personas",
        json={"record": _legacy_record(), "population_id": population_id},
        headers=headers,
    )
    assert persona.status_code == 200
    body = persona.json()
    assert body["legacy_persona_id"] == "0042"
    assert body["tenant_id"] == tenant_id
    assert body["population_id"] == population_id
    assert body["dimensions"]["role_function"] == "Teaching"

    listed = client.get("/api/v1/personas", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [body["id"]]

    fetched = client.get(f"/api/v1/personas/{body['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]


def test_list_endpoints_are_tenant_isolated() -> None:
    client = _client()
    alpha = client.post("/api/v1/tenants", json={"name": "Alpha", "slug": "alpha"}).json()
    bravo = client.post("/api/v1/tenants", json={"name": "Bravo", "slug": "bravo"}).json()
    client.post(
        "/api/v1/populations",
        json={"name": "Alpha workforce"},
        headers={"X-Tenant-Id": alpha["id"]},
    )
    client.post(
        "/api/v1/populations",
        json={"name": "Bravo workforce"},
        headers={"X-Tenant-Id": bravo["id"]},
    )
    client.post(
        "/api/v1/personas",
        json={"record": _legacy_record()},
        headers={"X-Tenant-Id": alpha["id"]},
    )

    alpha_pops = client.get(
        "/api/v1/populations", headers={"X-Tenant-Id": alpha["id"]}
    ).json()
    bravo_pops = client.get(
        "/api/v1/populations", headers={"X-Tenant-Id": bravo["id"]}
    ).json()
    assert [item["name"] for item in alpha_pops] == ["Alpha workforce"]
    assert [item["name"] for item in bravo_pops] == ["Bravo workforce"]

    bravo_personas = client.get(
        "/api/v1/personas", headers={"X-Tenant-Id": bravo["id"]}
    ).json()
    assert bravo_personas == []

    alpha_persona_id = client.get(
        "/api/v1/personas", headers={"X-Tenant-Id": alpha["id"]}
    ).json()[0]["id"]
    missing = client.get(
        f"/api/v1/personas/{alpha_persona_id}",
        headers={"X-Tenant-Id": bravo["id"]},
    )
    assert missing.status_code == 404


def test_missing_tenant_header_is_400() -> None:
    client = _client()
    client.post("/api/v1/tenants", json={"name": "Acme", "slug": "acme"})
    response = client.get("/api/v1/populations")
    assert response.status_code == 400
    assert "X-Tenant-Id" in response.json()["detail"]


def test_get_tenant_rejects_mismatched_header() -> None:
    client = _client()
    alpha = client.post("/api/v1/tenants", json={"name": "Alpha", "slug": "alpha"}).json()
    bravo = client.post("/api/v1/tenants", json={"name": "Bravo", "slug": "bravo"}).json()
    response = client.get(
        f"/api/v1/tenants/{alpha['id']}",
        headers={"X-Tenant-Id": bravo["id"]},
    )
    assert response.status_code == 403


def test_api_token_required_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MATRIX_ENTERPRISE_API_TOKEN", "secret-token")
    client = TestClient(create_enterprise_app(InMemoryEnterpriseStore()))

    denied = client.post("/api/v1/tenants", json={"name": "Acme", "slug": "acme"})
    assert denied.status_code == 401

    wrong = client.post(
        "/api/v1/tenants",
        json={"name": "Acme", "slug": "acme"},
        headers={"Authorization": "Bearer nope"},
    )
    assert wrong.status_code == 401

    ok = client.post(
        "/api/v1/tenants",
        json={"name": "Acme", "slug": "acme"},
        headers={"Authorization": "Bearer secret-token"},
    )
    assert ok.status_code == 200
    docs = client.get("/docs")
    assert docs.status_code == 200


def test_org_edges_and_10k_declaration_via_api() -> None:
    client = _client()
    tenant = client.post("/api/v1/tenants", json={"name": "Acme", "slug": "acme"}).json()
    headers = {"X-Tenant-Id": tenant["id"]}
    manager = client.post(
        "/api/v1/personas",
        json={"record": _legacy_record()},
        headers=headers,
    ).json()
    report_record = dict(_legacy_record())
    report_record["persona_id"] = "0043"
    report = client.post(
        "/api/v1/personas",
        json={"record": report_record},
        headers=headers,
    ).json()
    edge = client.post(
        "/api/v1/org-edges",
        json={
            "relation": "reports_to",
            "source_kind": "persona",
            "source_id": report["id"],
            "target_kind": "persona",
            "target_id": manager["id"],
        },
        headers=headers,
    )
    assert edge.status_code == 200
    assert edge.json()["relation"] == "reports_to"

    declared = client.post(
        "/api/v1/population-declarations",
        json={
            "name": "10k workforce",
            "target_size": 10000,
            "backend": "coreset_1m",
            "include_org_structure": True,
            "segments": [
                {"name": "engineering", "share": 0.4},
                {"name": "support", "share": 0.35},
                {"name": "other", "share": 0.25},
            ],
        },
        headers=headers,
    )
    assert declared.status_code == 200
    body = declared.json()
    assert body["target_size"] == 10000
    assert body["backend"] == "coreset_1m"
    assert sum(body["resolved_counts"]) == 10000
    fetched = client.get(
        f"/api/v1/populations/{body['population_id']}/declaration",
        headers=headers,
    )
    assert fetched.status_code == 200
    assert fetched.json()["resolved_counts"] == body["resolved_counts"]


def test_org_edge_and_declaration_are_tenant_isolated() -> None:
    client = _client()
    alpha = client.post("/api/v1/tenants", json={"name": "Alpha", "slug": "alpha"}).json()
    bravo = client.post("/api/v1/tenants", json={"name": "Bravo", "slug": "bravo"}).json()
    alpha_headers = {"X-Tenant-Id": alpha["id"]}
    persona = client.post(
        "/api/v1/personas",
        json={"record": _legacy_record()},
        headers=alpha_headers,
    ).json()
    edge = client.post(
        "/api/v1/org-edges",
        json={
            "relation": "owns_system",
            "source_kind": "persona",
            "source_id": persona["id"],
            "target_kind": "system",
            "target_id": "sys_erp",
        },
        headers=alpha_headers,
    ).json()
    assert (
        client.get("/api/v1/org-edges", headers={"X-Tenant-Id": bravo["id"]}).json()
        == []
    )
    missing = client.get(
        f"/api/v1/org-edges/{edge['id']}",
        headers={"X-Tenant-Id": bravo["id"]},
    )
    assert missing.status_code == 404

    declared = client.post(
        "/api/v1/population-declarations",
        json={
            "target_size": 10000,
            "backend": "full_dag",
            "segments": [{"name": "all", "count": 10000}],
        },
        headers=alpha_headers,
    ).json()
    other = client.get(
        f"/api/v1/populations/{declared['population_id']}/declaration",
        headers={"X-Tenant-Id": bravo["id"]},
    )
    assert other.status_code == 404


def test_experiment_create_estimate_and_harbor_job() -> None:
    client = _client()
    tenant = client.post("/api/v1/tenants", json={"name": "Acme", "slug": "acme"}).json()
    headers = {"X-Tenant-Id": tenant["id"]}
    created = client.post(
        "/api/v1/experiments",
        json={
            "hypothesis": "Novice users retry more often",
            "objective": "Measure retry rate",
            "random_seed": 42,
            "kind": "ab",
            "task_path": "application/tasks/example-survey_product-feedback",
            "sample_size": 4,
            "metrics": ["retry_rate"],
            "execution_budget": {"max_cost": 5.0, "max_concurrency": 2},
            "governance": {"retention_days": 14, "sign_off": "lead"},
        },
        headers=headers,
    )
    assert created.status_code == 200
    body = created.json()
    assert body["default_policy"] == "SANDBOX_ONLY"
    assert body["kind"] == "ab"
    assert body["random_seed"] == 42
    assert body["governance"]["sign_off"] == "lead"

    estimate = client.post(
        f"/api/v1/experiments/{body['id']}/estimate", headers=headers
    )
    assert estimate.status_code == 200
    assert estimate.json()["within_budget"] is True
    assert estimate.json()["decision"] == "SANDBOX_ONLY"
    assert estimate.json()["trial_count"] == 4

    mapped = client.get(
        f"/api/v1/experiments/{body['id']}/harbor-job", headers=headers
    )
    assert mapped.status_code == 200
    payload = mapped.json()
    assert payload["sidecar"]["seed"] == 42
    assert payload["sidecar"]["experiment_id"] == body["id"]
    assert payload["harbor_job"]["tasks"][0]["path"].endswith(
        "example-survey_product-feedback"
    )
    assert payload["harbor_job"]["job_name"].startswith("enterprise-")


def test_experiment_list_is_tenant_isolated() -> None:
    client = _client()
    alpha = client.post("/api/v1/tenants", json={"name": "Alpha", "slug": "alpha"}).json()
    bravo = client.post("/api/v1/tenants", json={"name": "Bravo", "slug": "bravo"}).json()
    created = client.post(
        "/api/v1/experiments",
        json={"hypothesis": "H", "objective": "O"},
        headers={"X-Tenant-Id": alpha["id"]},
    ).json()
    assert (
        client.get("/api/v1/experiments", headers={"X-Tenant-Id": bravo["id"]}).json()
        == []
    )
    missing = client.get(
        f"/api/v1/experiments/{created['id']}",
        headers={"X-Tenant-Id": bravo["id"]},
    )
    assert missing.status_code == 404


def test_invalid_persona_schema_is_400() -> None:
    client = _client()
    tenant = client.post("/api/v1/tenants", json={"name": "Acme", "slug": "acme"}).json()
    response = client.post(
        "/api/v1/personas",
        json={"record": {"persona_id": "x", "dimensions": {}}},
        headers={"X-Tenant-Id": tenant["id"]},
    )
    assert response.status_code == 400


def test_policy_evaluate_deny_allow_and_sandbox() -> None:
    client = _client()
    tenant = client.post("/api/v1/tenants", json={"name": "Acme", "slug": "acme"}).json()
    headers = {"X-Tenant-Id": tenant["id"]}

    defaulted = client.post(
        "/api/v1/policy/evaluate",
        json={"action": "complete", "resource": "model.complete"},
        headers=headers,
    )
    assert defaulted.status_code == 200
    assert defaulted.json()["decision"] == "SANDBOX_ONLY"

    denied = client.post(
        "/api/v1/policy/evaluate",
        json={
            "action": "complete",
            "resource": "model.complete",
            "model_provider": "anthropic",
            "data_classification": "RESTRICTED",
            "destination": "external",
        },
        headers=headers,
    )
    assert denied.status_code == 200
    assert denied.json()["decision"] == "DENY"

    client.put(
        "/api/v1/model-policy",
        json={
            "allow_external": True,
            "allowed_providers": ["anthropic"],
        },
        headers=headers,
    )
    allowed = client.post(
        "/api/v1/policy/evaluate",
        json={
            "action": "complete",
            "resource": "model.complete",
            "model_provider": "anthropic",
            "data_classification": "PUBLIC",
            "destination": "external",
        },
        headers=headers,
    )
    assert allowed.status_code == 200
    assert allowed.json()["decision"] == "ALLOW"


def test_model_complete_denies_and_sandboxes() -> None:
    client = _client()
    tenant = client.post("/api/v1/tenants", json={"name": "Acme", "slug": "acme"}).json()
    headers = {"X-Tenant-Id": tenant["id"]}

    sandbox = client.post(
        "/api/v1/models/complete",
        json={"messages": [{"role": "user", "content": "hello"}]},
        headers=headers,
    )
    assert sandbox.status_code == 200
    body = sandbox.json()
    assert body["decision"] == "SANDBOX_ONLY"
    assert body["provider"] == "sandbox"
    assert "hello" in body["content"]

    denied = client.post(
        "/api/v1/models/complete",
        json={
            "model_provider": "openai",
            "data_classification": "RESTRICTED",
            "messages": [{"role": "user", "content": "secret"}],
        },
        headers=headers,
    )
    assert denied.status_code == 403
    assert denied.json()["decision"] == "DENY"

    policy = client.get("/api/v1/model-policy", headers=headers)
    assert policy.status_code == 200
    assert policy.json()["default_decision"] == "SANDBOX_ONLY"

    catalog = client.get("/api/v1/models/catalog", headers=headers)
    assert catalog.status_code == 200
    names = {item["name"] for item in catalog.json()}
    assert "sandbox" in names
    assert "anthropic" in names


def test_model_policy_is_tenant_isolated() -> None:
    client = _client()
    alpha = client.post("/api/v1/tenants", json={"name": "Alpha", "slug": "alpha"}).json()
    bravo = client.post("/api/v1/tenants", json={"name": "Bravo", "slug": "bravo"}).json()
    client.put(
        "/api/v1/model-policy",
        json={"allow_external": True, "allowed_providers": ["anthropic"]},
        headers={"X-Tenant-Id": alpha["id"]},
    )
    bravo_policy = client.get(
        "/api/v1/model-policy", headers={"X-Tenant-Id": bravo["id"]}
    ).json()
    assert bravo_policy["allow_external"] is False
    assert bravo_policy["allowed_providers"] == []

    alpha_eval = client.post(
        "/api/v1/policy/evaluate",
        json={
            "action": "complete",
            "resource": "model.complete",
            "model_provider": "anthropic",
            "data_classification": "PUBLIC",
            "destination": "external",
        },
        headers={"X-Tenant-Id": alpha["id"]},
    ).json()
    bravo_eval = client.post(
        "/api/v1/policy/evaluate",
        json={
            "action": "complete",
            "resource": "model.complete",
            "model_provider": "anthropic",
            "data_classification": "PUBLIC",
            "destination": "external",
        },
        headers={"X-Tenant-Id": bravo["id"]},
    ).json()
    assert alpha_eval["decision"] == "ALLOW"
    assert bravo_eval["decision"] == "SANDBOX_ONLY"
