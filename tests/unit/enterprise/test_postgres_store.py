"""PostgreSQL store against an embedded server (skipped when unavailable)."""

from __future__ import annotations

import tempfile

import pytest

from matraix.enterprise.domain import Budget, Workspace
from matraix.enterprise.errors import CrossTenantAccessError, EntityNotFoundError
from matraix.enterprise.ids import EntityKind, OrganizationId, PopulationId, new_id
from matraix.enterprise.records import ConcurrencyError, new_work_item
from matraix.enterprise.store import create_tenant_with_default_org

pgserver = pytest.importorskip("pgserver")
pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def postgres_dsn():
    directory = tempfile.mkdtemp(prefix="agenttwin-pg-")
    try:
        server = pgserver.get_server(directory)
    except Exception as exc:  # noqa: BLE001 - environment-specific
        pytest.skip(f"embedded postgres unavailable: {exc}")
    try:
        yield server.get_uri()
    finally:
        server.cleanup()


@pytest.fixture
def store(postgres_dsn):
    from matraix.enterprise.postgres_store import PostgresEnterpriseStore

    item = PostgresEnterpriseStore(postgres_dsn)
    yield item
    item.close()


def test_postgres_migrations_and_legacy_protocol(store) -> None:
    from matraix.enterprise.entities import Population
    from matraix.enterprise.migrations import LATEST_SCHEMA_VERSION

    with store._conn.cursor() as cursor:  # noqa: SLF001 - schema assertion
        cursor.execute("SELECT MAX(version) FROM schema_migrations")
        assert cursor.fetchone()["max"] == LATEST_SCHEMA_VERSION

    slug = f"acme-{new_id(EntityKind.TENANT)[-6:]}"
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug=slug)
    other, _ = create_tenant_with_default_org(store, name="Bravo", slug=f"bravo-{new_id(EntityKind.TENANT)[-6:]}")
    population = Population(
        id=PopulationId(tenant.id, new_id(EntityKind.POPULATION)),
        tenant_id=tenant.id,
        organization_id=org.id,
        name="Workforce",
        target_size=10,
    )
    store.put_population(population)
    assert store.get_population(tenant.id, population.id).name == "Workforce"
    assert [item.name for item in store.list_populations(tenant.id)] == ["Workforce"]
    assert store.list_populations(other.id) == []
    with pytest.raises(CrossTenantAccessError):
        store.get_population(other.id, population.id)
    with pytest.raises(EntityNotFoundError):
        store.get_organization(other.id, OrganizationId(other.id, org.id.value))


def test_postgres_records_work_items_and_isolation(store) -> None:
    tenant, _ = create_tenant_with_default_org(store, name="A", slug=f"a-{new_id(EntityKind.TENANT)[-6:]}")
    other, _ = create_tenant_with_default_org(store, name="B", slug=f"b-{new_id(EntityKind.TENANT)[-6:]}")
    t, o = tenant.id.value, other.id.value

    workspace = store.put_record(Workspace.create(tenant_id=t, name="ws"))
    store.put_record(workspace.with_update(name="ws2"))
    with pytest.raises(ConcurrencyError):
        store.put_record(workspace.with_update(name="stale"))
    with pytest.raises(EntityNotFoundError):
        store.get_record(o, EntityKind.WORKSPACE, workspace.id, Workspace)
    assert store.list_records(o, EntityKind.WORKSPACE, Workspace) == []

    items = [new_work_item(tenant_id=t, run_id="run", shard_id="s", seq=i, payload={}, idempotency_key=f"run:{i}") for i in range(3)]
    assert store.add_work_items(items) == 3
    assert store.add_work_items(items) == 0
    leased = store.lease_work_items(t, owner="w", limit=5, lease_seconds=30)
    assert [item.seq for item in leased] == [0, 1, 2]
    assert store.lease_work_items(o, owner="w", limit=5, lease_seconds=30) == []
    done = store.complete_work_item(t, leased[0].id, owner="w", status="completed", result_ref="tri")
    assert done.status == "completed"

    with pytest.raises(RuntimeError):
        with store.transaction():
            store.put_record(Budget.create(tenant_id=t, name="x", limit_usd=1.0))
            raise RuntimeError("abort")
    assert store.count_records(t, EntityKind.BUDGET) == 0
