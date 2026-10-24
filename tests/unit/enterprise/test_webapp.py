"""AgentTwin static web app served by the enterprise API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from matraix.enterprise.api import _agenttwin_webapp_dir, create_enterprise_app
from matraix.enterprise.repositories import InMemoryEnterpriseStore

REPO_WEBAPP = Path(__file__).resolve().parents[3] / "apps" / "agenttwin"
SECRET_MARKERS = (
    "sk-ant-",
    "sk-proj-",
    "MATRIX_ENTERPRISE_API_TOKEN=",
    "Authorization: Bearer ",
)


def _client(monkeypatch: pytest.MonkeyPatch | None = None) -> TestClient:
    if monkeypatch is not None:
        monkeypatch.delenv("MATRIX_ENTERPRISE_API_TOKEN", raising=False)
        monkeypatch.delenv("MATRIX_ENTERPRISE_AUTH_MODE", raising=False)
    return TestClient(create_enterprise_app(InMemoryEnterpriseStore()))


def test_webapp_dir_resolves_checkout() -> None:
    found = _agenttwin_webapp_dir()
    assert found is not None
    assert (found / "index.html").is_file()
    assert found.resolve() == REPO_WEBAPP.resolve()


def test_root_redirects_to_app() -> None:
    client = _client()
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/app/"


def test_app_index_is_public_and_honest() -> None:
    client = _client()
    response = client.get("/app/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    body = response.text
    assert "AgentTwin" in body
    assert "Synthetic users are not human research" in body
    assert "SANDBOX_ONLY" in body
    for marker in SECRET_MARKERS:
        assert marker not in body


def test_app_assets_contain_no_baked_secrets() -> None:
    client = _client()
    js = client.get("/app/app.js")
    css = client.get("/app/app.css")
    assert js.status_code == 200
    assert css.status_code == 200
    source = js.text
    assert "/api/v1/tenants" in source
    assert "e.actor" in source
    assert "e.timestamp" in source
    assert "synthetic_equivalent_to_human_research" in source
    assert "matraix run" in source
    for marker in SECRET_MARKERS:
        assert marker not in source
        assert marker not in css.text


def test_app_stays_public_when_api_requires_a_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MATRIX_ENTERPRISE_API_TOKEN", "test-only-token")
    client = TestClient(create_enterprise_app(InMemoryEnterpriseStore()))
    page = client.get("/app/")
    assert page.status_code == 200
    assert "AgentTwin" in page.text
    denied = client.get("/api/v1/whoami")
    assert denied.status_code == 401
    allowed = client.get(
        "/api/v1/whoami", headers={"Authorization": "Bearer test-only-token"}
    )
    assert allowed.status_code == 200
    assert allowed.json()["platform_admin"] is True


def test_mount_does_not_break_tenant_create() -> None:
    client = _client()
    created = client.post(
        "/api/v1/tenants", json={"name": "Acme Research", "slug": "acme"}
    )
    assert created.status_code == 200
    assert created.json()["slug"] == "acme"
    health = client.get("/health")
    assert health.status_code == 200
