"""Phase 9 IAM: OIDC patterns, RBAC, append-only audit, CSRF, rate limits."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from matraix.enterprise import (
    AppendOnlyAuditError,
    AuthorizationError,
    CrossTenantAccessError,
    InMemoryEnterpriseStore,
    Permission,
    Principal,
    Role,
    SqliteEnterpriseStore,
    authorize,
    create_tenant_with_default_org,
    mint_dev_jwt,
    new_audit_event,
    oidc_metadata,
    permission_for,
)
from matraix.enterprise.api import create_enterprise_app
from matraix.enterprise.http_security import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from matraix.enterprise.identity import EnterpriseUser
from matraix.enterprise.ids import EntityKind, UserId, new_id
from matraix.enterprise.oidc import OIDC_DEV_SECRET_ENV


def _client(store=None) -> TestClient:
    return TestClient(create_enterprise_app(store or InMemoryEnterpriseStore()))


def test_security_modules_have_no_hardcoded_secrets() -> None:
    import matraix.enterprise.http_security as http_security
    import matraix.enterprise.oidc as oidc

    for module in (http_security, oidc):
        source = open(module.__file__, encoding="utf-8").read()
        assert "sk-" not in source
        assert "BEGIN PRIVATE KEY" not in source
        assert "MATRIX_ENTERPRISE_OIDC_DEV_SECRET" in source or module is http_security


def test_oidc_metadata_is_a_pattern_not_a_live_idp() -> None:
    body = oidc_metadata()
    assert body["live_idp_required"] is False
    assert "S256" in body["code_challenge_methods_supported"]
    assert "authorization_code" in body["grant_types_supported"]


def test_viewer_cannot_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OIDC_DEV_SECRET_ENV, "unit-test-oidc-secret")
    monkeypatch.setenv("MATRIX_ENTERPRISE_OIDC_ISSUER", "https://idp.example.test")
    monkeypatch.setenv("MATRIX_ENTERPRISE_OIDC_AUDIENCE", "agenttwin-enterprise")
    client = _client()
    tenant = client.post("/api/v1/tenants", json={"name": "Acme", "slug": "acme"}).json()
    token = mint_dev_jwt(
        subject="viewer@example.test",
        tenant_id=tenant["id"],
        roles=["viewer"],
    )
    headers = {
        "X-Tenant-Id": tenant["id"],
        "Authorization": f"Bearer {token}",
    }
    created = client.post(
        "/api/v1/experiments",
        json={"hypothesis": "H", "objective": "O"},
        headers=headers,
    )
    assert created.status_code == 403
    listed = client.get("/api/v1/experiments", headers=headers)
    assert listed.status_code == 200


def test_oidc_researcher_is_tenant_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OIDC_DEV_SECRET_ENV, "unit-test-oidc-secret")
    monkeypatch.setenv("MATRIX_ENTERPRISE_OIDC_ISSUER", "https://idp.example.test")
    monkeypatch.setenv("MATRIX_ENTERPRISE_OIDC_AUDIENCE", "agenttwin-enterprise")
    client = _client()
    alpha = client.post("/api/v1/tenants", json={"name": "Alpha", "slug": "alpha"}).json()
    bravo = client.post("/api/v1/tenants", json={"name": "Bravo", "slug": "bravo"}).json()
    token = mint_dev_jwt(
        subject="researcher@example.test",
        tenant_id=alpha["id"],
        roles=["researcher"],
    )
    denied = client.get(
        "/api/v1/experiments",
        headers={
            "X-Tenant-Id": bravo["id"],
            "Authorization": f"Bearer {token}",
        },
    )
    assert denied.status_code == 403
    allowed = client.get(
        "/api/v1/experiments",
        headers={
            "X-Tenant-Id": alpha["id"],
            "Authorization": f"Bearer {token}",
        },
    )
    assert allowed.status_code == 200


def test_audit_is_append_only_and_tenant_scoped() -> None:
    store = InMemoryEnterpriseStore()
    alpha, _ = create_tenant_with_default_org(store, name="Alpha", slug="alpha")
    bravo, _ = create_tenant_with_default_org(store, name="Bravo", slug="bravo")
    event = store.append_audit(
        new_audit_event(
            actor="alice",
            action="POST /api/v1/experiments",
            resource="/api/v1/experiments",
            result="200",
            tenant_id=alpha.id,
        )
    )
    assert store.list_audit(alpha.id)[0].id == event.id
    assert store.list_audit(bravo.id) == []
    assert store.export_audit(bravo.id) == []
    with pytest.raises(AppendOnlyAuditError):
        store.update_audit(event.id, result="rewritten")
    with pytest.raises(AppendOnlyAuditError):
        store.delete_audit(event.id)
    assert store.list_audit(alpha.id)[0].result == "200"


def test_sqlite_audit_triggers_reject_update(tmp_path) -> None:
    store = SqliteEnterpriseStore(tmp_path / "sec.sqlite")
    tenant, _ = create_tenant_with_default_org(store, name="Acme", slug="acme")
    event = store.append_audit(
        new_audit_event(
            actor="alice",
            action="GET /api/v1/audit",
            resource="/api/v1/audit",
            result="200",
            tenant_id=tenant.id,
        )
    )
    with pytest.raises(Exception):
        store._conn.execute(
            "UPDATE audit_events SET result = 'nope' WHERE id = ?",
            (event.id,),
        )
    with pytest.raises(AppendOnlyAuditError):
        store.delete_audit(event.id)
    store.close()


def test_user_storage_is_tenant_isolated() -> None:
    store = InMemoryEnterpriseStore()
    alpha, _ = create_tenant_with_default_org(store, name="Alpha", slug="alpha")
    bravo, _ = create_tenant_with_default_org(store, name="Bravo", slug="bravo")
    user = store.put_user(
        EnterpriseUser(
            id=UserId(alpha.id, new_id(EntityKind.USER)),
            tenant_id=alpha.id,
            username="alice",
            roles=(Role.VIEWER,),
        )
    )
    with pytest.raises(CrossTenantAccessError):
        store.get_user(bravo.id, user.id)
    assert store.list_users(bravo.id) == []


def test_api_audit_export_and_governance() -> None:
    client = _client()
    tenant = client.post("/api/v1/tenants", json={"name": "Acme", "slug": "acme"}).json()
    headers = {"X-Tenant-Id": tenant["id"]}
    client.get("/api/v1/experiments", headers=headers)
    events = client.get("/api/v1/audit", headers=headers)
    assert events.status_code == 200
    assert any(item["resource"] == "/api/v1/experiments" for item in events.json())
    exported = client.get("/api/v1/audit/export", headers=headers)
    assert exported.status_code == 200
    assert exported.json()["schema_version"] == "EnterpriseAuditExport.v1"
    created = client.post(
        "/api/v1/governance/reviews",
        json={
            "kind": "ingestion",
            "title": "HRIS extract review",
            "notes": "Aggregate stats only",
            "status": "approved",
            "sign_off": True,
        },
        headers=headers,
    )
    assert created.status_code == 200
    assert created.json()["kind"] == "ingestion"
    listed = client.get("/api/v1/governance/reviews", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["title"] == "HRIS extract review"


def test_scim_user_hook() -> None:
    client = _client()
    tenant = client.post("/api/v1/tenants", json={"name": "Acme", "slug": "acme"}).json()
    headers = {"X-Tenant-Id": tenant["id"]}
    created = client.post(
        "/api/v1/scim/Users",
        json={
            "userName": "casey",
            "emails": [{"value": "casey@example.test"}],
            "roles": [{"value": "viewer"}],
            "active": True,
        },
        headers=headers,
    )
    assert created.status_code == 200
    assert created.json()["userName"] == "casey"
    listed = client.get("/api/v1/scim/Users", headers=headers)
    assert listed.json()["totalResults"] == 1


def test_csrf_required_when_session_cookie_present() -> None:
    client = _client()
    tenant = client.post("/api/v1/tenants", json={"name": "Acme", "slug": "acme"}).json()
    session = client.post(
        "/api/v1/auth/session",
        json={"subject": "casey", "tenant_id": tenant["id"], "roles": ["tenant_admin"]},
    )
    assert session.status_code == 200
    csrf = session.json()["csrf_token"]
    denied = client.post(
        "/api/v1/populations",
        json={"name": "Workforce"},
        headers={"X-Tenant-Id": tenant["id"]},
    )
    assert denied.status_code == 403
    allowed = client.post(
        "/api/v1/populations",
        json={"name": "Workforce"},
        headers={
            "X-Tenant-Id": tenant["id"],
            CSRF_HEADER: csrf,
        },
    )
    assert allowed.status_code == 200


def test_rate_limit_returns_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MATRIX_ENTERPRISE_RATE_LIMIT", "2")
    monkeypatch.setenv("MATRIX_ENTERPRISE_SENSITIVE_RATE_LIMIT", "2")
    monkeypatch.setenv("MATRIX_ENTERPRISE_RATE_WINDOW_SECONDS", "60")
    from matraix.enterprise import rate_limit as rl

    rl._LIMITER._hits.clear()
    client = _client()
    first = client.post("/api/v1/tenants", json={"name": "A", "slug": "a1"})
    second = client.post("/api/v1/tenants", json={"name": "B", "slug": "b1"})
    third = client.post("/api/v1/tenants", json={"name": "C", "slug": "c1"})
    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429


def test_authorize_abac_restricted() -> None:
    viewer = Principal(
        subject="v",
        source="oidc",
        roles=(Role.VIEWER,),
        attributes={"data_classification": "RESTRICTED"},
    )
    with pytest.raises(AuthorizationError):
        authorize(viewer, Permission.PERSONA_READ, attributes={"data_classification": "RESTRICTED"})
    admin = Principal(subject="a", source="oidc", roles=(Role.PLATFORM_ADMIN,))
    authorize(
        admin,
        Permission.PERSONA_READ,
        attributes={"data_classification": "RESTRICTED"},
    )


def test_permission_map_covers_hardening_routes() -> None:
    assert permission_for("GET", "/api/v1/audit") is Permission.AUDIT_READ
    assert permission_for("POST", "/api/v1/scim/Users") is Permission.IDENTITY_WRITE
    assert permission_for("POST", "/api/v1/experiments/x/execute") is (
        Permission.EXPERIMENT_EXECUTE
    )
    assert permission_for("GET", "/health") is None
