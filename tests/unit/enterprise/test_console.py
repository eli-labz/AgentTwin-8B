"""Enterprise console nav, wizard, CORS, and auth hardening."""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from matraix.enterprise import CONSOLE_NAV, WIZARD_STEPS, console_manifest
from matraix.enterprise.api import create_enterprise_app
from matraix.enterprise.http_security import resolve_cors_origins
from matraix.enterprise.repositories import InMemoryEnterpriseStore


def test_console_nav_and_wizard_are_complete() -> None:
    assert [item[1] for item in CONSOLE_NAV] == [
        "Overview",
        "Organizations",
        "Populations",
        "Personas",
        "Experiments",
        "Tasks",
        "Environments",
        "Models",
        "Evaluations",
        "Analytics",
        "Governance",
        "Audit",
        "Infrastructure",
        "Settings",
    ]
    assert [item[1] for item in WIZARD_STEPS] == [
        "Population",
        "Scenario",
        "Task",
        "Environment",
        "AI System",
        "Metrics",
        "Governance",
        "Scale",
        "Cost",
        "Launch",
    ]
    body = console_manifest()
    assert body["synthetic_equivalent_to_human_research"] is False
    assert body["recommended_human_validation"] is True
    assert body["default_policy"] == "SANDBOX_ONLY"
    assert "not equivalent to human" in body["limitation"]


def test_console_html_and_manifest_are_public() -> None:
    client = TestClient(create_enterprise_app(InMemoryEnterpriseStore()))
    page = client.get("/console")
    assert page.status_code == 200
    assert "AgentTwin Enterprise" in page.text
    assert "not" in page.text.lower() and "human research" in page.text.lower()
    assert "/api/v1/executions/" in page.text and "/report?format=" in page.text
    assert "recommended_human_validation" in page.text
    spec = client.get("/openapi.json").json()
    assert "/api/v1/console/manifest" in spec["paths"]
    manifest = client.get("/api/v1/console/manifest")
    assert manifest.status_code == 200
    assert manifest.json()["synthetic_equivalent_to_human_research"] is False


def test_production_cors_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MATRIX_ENTERPRISE_ENV", "production")
    monkeypatch.delenv("MATRIX_ENTERPRISE_CORS_ORIGINS", raising=False)
    assert resolve_cors_origins() == []
    client = TestClient(create_enterprise_app(InMemoryEnterpriseStore()))
    preflight = client.options(
        "/api/v1/tenants",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in {
        key.lower() for key in preflight.headers
    }


def test_dev_cors_allows_vite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MATRIX_ENTERPRISE_ENV", "dev")
    monkeypatch.delenv("MATRIX_ENTERPRISE_CORS_ORIGINS", raising=False)
    origins = resolve_cors_origins()
    assert "http://localhost:5173" in origins
    client = TestClient(create_enterprise_app(InMemoryEnterpriseStore()))
    preflight = client.options(
        "/api/v1/tenants",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_require_auth_without_token_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MATRIX_ENTERPRISE_REQUIRE_AUTH", "1")
    monkeypatch.delenv("MATRIX_ENTERPRISE_API_TOKEN", raising=False)
    client = TestClient(create_enterprise_app(InMemoryEnterpriseStore()))
    denied = client.get("/api/v1/tenants")
    assert denied.status_code == 401
    assert client.get("/health").status_code == 200
    assert client.get("/console").status_code == 200
