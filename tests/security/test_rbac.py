"""Authz failures and cross-tenant deny for Phase 9."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from matraix.enterprise.api import create_enterprise_app
from matraix.enterprise.oidc import OIDC_DEV_SECRET_ENV, mint_dev_jwt
from matraix.enterprise.repositories import InMemoryEnterpriseStore


def test_authenticated_viewer_is_denied_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OIDC_DEV_SECRET_ENV, "security-test-oidc-secret")
    monkeypatch.setenv("MATRIX_ENTERPRISE_OIDC_ISSUER", "https://idp.example.test")
    monkeypatch.setenv("MATRIX_ENTERPRISE_OIDC_AUDIENCE", "agenttwin-enterprise")
    client = TestClient(create_enterprise_app(InMemoryEnterpriseStore()))
    tenant = client.post("/api/v1/tenants", json={"name": "Acme", "slug": "acme"}).json()
    token = mint_dev_jwt(
        subject="viewer",
        tenant_id=tenant["id"],
        roles=["viewer"],
    )
    denied = client.post(
        "/api/v1/populations",
        json={"name": "Nope"},
        headers={
            "X-Tenant-Id": tenant["id"],
            "Authorization": f"Bearer {token}",
        },
    )
    assert denied.status_code == 403
    assert denied.json()["permission"] == "population:write"
