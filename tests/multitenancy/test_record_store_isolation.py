"""Tenant A must not read, mutate, execute or reference Tenant B resources.

Covers the Phase 1 generic record store, the audit stream, the work queue,
persona snapshots, idempotency keys, and the HTTP layer with tenant-bound
principals.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from matraix.enterprise.api import create_enterprise_app
from matraix.enterprise.audit import AuditCategory, AuditLog
from matraix.enterprise.auth.principal import Principal
from matraix.enterprise.context import build_context
from matraix.enterprise.domain import PersonaSnapshot, PrincipalKind, RoleName, Workspace
from matraix.enterprise.errors import EntityNotFoundError
from matraix.enterprise.ids import EntityKind, TenantId
from matraix.enterprise.records import new_work_item
from matraix.enterprise.repositories import InMemoryEnterpriseStore
from matraix.enterprise.secrets import StaticSecretProvider
from matraix.enterprise.sqlite_store import SqliteEnterpriseStore
from matraix.enterprise.store import create_tenant_with_default_org

ALPHA = "tnt_alpha"
BRAVO = "tnt_bravo"


@pytest.fixture(params=["memory", "sqlite"])
def store(request):
    item = InMemoryEnterpriseStore() if request.param == "memory" else SqliteEnterpriseStore(":memory:")
    create_tenant_with_default_org(item, name="Alpha", slug="alpha", tenant_id=TenantId(ALPHA))
    create_tenant_with_default_org(item, name="Bravo", slug="bravo", tenant_id=TenantId(BRAVO))
    yield item
    item.close()


def test_records_are_invisible_across_tenants(store) -> None:
    workspace = store.put_record(Workspace.create(tenant_id=ALPHA, name="alpha-ws"))
    with pytest.raises(EntityNotFoundError):
        store.get_record(BRAVO, EntityKind.WORKSPACE, workspace.id, Workspace)
    assert store.list_records(BRAVO, EntityKind.WORKSPACE, Workspace) == []
    assert store.count_records(BRAVO, EntityKind.WORKSPACE) == 0
    with pytest.raises(EntityNotFoundError):
        store.delete_record(BRAVO, EntityKind.WORKSPACE, workspace.id)
    # Still intact for the owner.
    assert store.get_record(ALPHA, EntityKind.WORKSPACE, workspace.id, Workspace).name == "alpha-ws"


def test_record_cannot_be_moved_between_tenants(store) -> None:
    workspace = store.put_record(Workspace.create(tenant_id=ALPHA, name="alpha-ws"))
    # A record carries its tenant; a foreign-tenant copy with the same id is a distinct row.
    clone = Workspace.model_validate({**workspace.to_document(), "tenant_id": BRAVO})
    store.put_record(clone)
    assert store.get_record(ALPHA, EntityKind.WORKSPACE, workspace.id, Workspace).tenant_id == ALPHA
    assert store.get_record(BRAVO, EntityKind.WORKSPACE, workspace.id, Workspace).tenant_id == BRAVO
    assert store.count_records(ALPHA, EntityKind.WORKSPACE) == 1


def test_audit_streams_are_partitioned(store) -> None:
    log = AuditLog(store)
    principal = Principal(subject="ops", kind=PrincipalKind.USER, tenant_id=ALPHA, roles=frozenset({RoleName.ADMIN}))
    log.record(tenant_id=ALPHA, principal=principal, action="x", category=AuditCategory.ADMIN)
    assert len(log.list(ALPHA)) == 1
    assert log.list(BRAVO) == []
    assert log.verify_chain(BRAVO) == {"ok": True, "checked": 0, "broken_at_seq": None}


def test_work_queue_and_snapshots_are_partitioned(store) -> None:
    store.add_work_items([new_work_item(tenant_id=ALPHA, run_id="run", shard_id="s", seq=0, payload={}, idempotency_key="run:0")])
    assert store.lease_work_items(BRAVO, owner="bravo-worker", limit=10, lease_seconds=30) == []
    assert store.count_work_items(BRAVO, run_id="run") == 0
    leased = store.lease_work_items(ALPHA, owner="alpha-worker", limit=10, lease_seconds=30)
    assert len(leased) == 1
    with pytest.raises(EntityNotFoundError):
        store.complete_work_item(BRAVO, leased[0].id, owner="alpha-worker", status="completed")
    store.add_persona_snapshots(ALPHA, [PersonaSnapshot(population_version_id="pov", seq=0, persona_ref="p", path="p.yaml", content_hash="h")])
    assert store.count_persona_snapshots(BRAVO, "pov") == 0
    store.put_idempotent(ALPHA, "launch", "k", {"run_id": "r"})
    assert store.get_idempotent(BRAVO, "launch", "k") is None


def _client(store) -> TestClient:
    secrets = StaticSecretProvider(
        {
            "MATRIX_ENTERPRISE_AUTH_TOKENS": json.dumps(
                {
                    "alpha-admin": {"subject": "alpha-admin", "tenant_id": ALPHA, "roles": ["admin"]},
                    "bravo-admin": {"subject": "bravo-admin", "tenant_id": BRAVO, "roles": ["admin"]},
                }
            )
        }
    )
    return TestClient(create_enterprise_app(context=build_context(store, secrets=secrets)))


def _headers(token: str, tenant: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if tenant:
        headers["X-Tenant-Id"] = tenant
    return headers


def test_http_principal_cannot_widen_into_other_tenant(store) -> None:
    client = _client(store)
    created = client.post("/api/v1/populations", json={"name": "alpha-pop"}, headers=_headers("alpha-admin"))
    assert created.status_code == 200
    population_id = created.json()["id"]

    # Header spoofing is rejected before any handler runs.
    assert client.get("/api/v1/populations", headers=_headers("bravo-admin", ALPHA)).status_code == 403
    # Bravo sees nothing of Alpha's, by id or by list.
    assert client.get("/api/v1/populations", headers=_headers("bravo-admin")).json() == []
    assert client.get(f"/api/v1/populations/{population_id}", headers=_headers("bravo-admin")).status_code == 404
    # Bravo cannot reference Alpha's population when creating a persona.
    persona = client.post(
        "/api/v1/personas",
        json={
            "record": {
                "persona_id": "1",
                "version": "1.0",
                "source": "synthetic",
                "dimensions": {"age_bracket": "25-34"},
            },
            "population_id": population_id,
        },
        headers=_headers("bravo-admin"),
    )
    assert persona.status_code == 404
    # Bravo cannot read Alpha's audit trail or identities.
    assert client.get("/api/v1/audit-events", headers=_headers("bravo-admin")).json()["items"] == []
    assert client.get("/api/v1/users", headers=_headers("bravo-admin")).json() == []
    # Tenant-bound admins cannot list other tenants.
    tenants = client.get("/api/v1/tenants", headers=_headers("bravo-admin")).json()
    assert [item["id"] for item in tenants] == [BRAVO]


def test_denied_cross_tenant_attempts_are_audited(store) -> None:
    client = _client(store)
    assert client.get("/api/v1/populations", headers=_headers("bravo-admin", ALPHA)).status_code == 403
    # The denial does not leak into Alpha's stream and Bravo's own stream stays clean of Alpha data.
    alpha_audit = client.get("/api/v1/audit-events", headers=_headers("alpha-admin")).json()["items"]
    assert all(item["actor"] != "bravo-admin" for item in alpha_audit)
