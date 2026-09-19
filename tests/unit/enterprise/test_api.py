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


def test_invalid_persona_schema_is_400() -> None:
    client = _client()
    tenant = client.post("/api/v1/tenants", json={"name": "Acme", "slug": "acme"}).json()
    response = client.post(
        "/api/v1/personas",
        json={"record": {"persona_id": "x", "dimensions": {}}},
        headers={"X-Tenant-Id": tenant["id"]},
    )
    assert response.status_code == 400
