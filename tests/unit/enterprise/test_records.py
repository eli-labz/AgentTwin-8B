"""Generic record store semantics shared by the in-memory and SQLite backends."""

from __future__ import annotations

import pytest

from matraix.enterprise.domain import Budget, PersonaSnapshot, Workspace
from matraix.enterprise.errors import EntityNotFoundError
from matraix.enterprise.ids import EntityKind, TenantId
from matraix.enterprise.records import ConcurrencyError, RecordQuery, new_work_item
from matraix.enterprise.repositories import InMemoryEnterpriseStore
from matraix.enterprise.sqlite_store import SqliteEnterpriseStore
from matraix.enterprise.store import create_tenant_with_default_org


@pytest.fixture(params=["memory", "sqlite"])
def store(request):
    if request.param == "memory":
        yield InMemoryEnterpriseStore()
    else:
        item = SqliteEnterpriseStore(":memory:")
        yield item
        item.close()


def _tenant(store, slug: str) -> str:
    tenant, _ = create_tenant_with_default_org(store, name=slug.title(), slug=slug, tenant_id=TenantId(f"tnt_{slug}"))
    return tenant.id.value


def test_put_get_list_and_optimistic_concurrency(store) -> None:
    tenant = _tenant(store, "alpha")
    workspace = Workspace.create(tenant_id=tenant, name="R&D")
    store.put_record(workspace)
    assert store.get_record(tenant, EntityKind.WORKSPACE, workspace.id, Workspace).name == "R&D"

    updated = store.put_record(workspace.with_update(name="Platform"))
    assert updated.version == 2
    with pytest.raises(ConcurrencyError):
        store.put_record(workspace.with_update(name="stale write"))
    with pytest.raises(ConcurrencyError):
        store.put_record(updated.with_update(name="explicit"), expected_version=1)

    budget = Budget.create(tenant_id=tenant, name="ops", limit_usd=25.0)
    store.put_record(budget)
    assert store.count_records(tenant, EntityKind.BUDGET) == 1
    found = store.list_records(tenant, EntityKind.BUDGET, Budget, RecordQuery(where={"name": "ops"}))
    assert [item.id for item in found] == [budget.id]
    assert store.list_records(tenant, EntityKind.BUDGET, Budget, RecordQuery(status="archived")) == []

    store.delete_record(tenant, EntityKind.BUDGET, budget.id)
    with pytest.raises(EntityNotFoundError):
        store.get_record(tenant, EntityKind.BUDGET, budget.id, Budget)


def test_list_pagination_and_ordering(store) -> None:
    tenant = _tenant(store, "alpha")
    for index in range(5):
        store.put_record(Budget.create(tenant_id=tenant, name=f"b{index}", limit_usd=float(index)))
    page = store.list_records(tenant, EntityKind.BUDGET, Budget, RecordQuery(limit=2, offset=1))
    assert [item.name for item in page] == ["b1", "b2"]
    newest = store.list_records(tenant, EntityKind.BUDGET, Budget, RecordQuery(order_by="created_at", descending=True, limit=1))
    assert newest[0].name == "b4"


def test_work_item_lease_heartbeat_complete_and_requeue(store) -> None:
    tenant = _tenant(store, "alpha")
    items = [
        new_work_item(tenant_id=tenant, run_id="run_1", shard_id="shard_0", seq=index, payload={"i": index}, idempotency_key=f"run_1:{index}")
        for index in range(4)
    ]
    assert store.add_work_items(items) == 4
    assert store.add_work_items(items) == 0  # idempotent dispatch

    leased = store.lease_work_items(tenant, owner="worker-1", limit=2, lease_seconds=30)
    assert [item.seq for item in leased] == [0, 1]
    assert all(item.status == "leased" and item.attempts == 1 for item in leased)
    # A second worker cannot lease the same items while the lease is live.
    others = store.lease_work_items(tenant, owner="worker-2", limit=10, lease_seconds=30)
    assert [item.seq for item in others] == [2, 3]
    assert store.heartbeat_work_item(tenant, leased[0].id, owner="worker-1", lease_seconds=30)
    assert not store.heartbeat_work_item(tenant, leased[0].id, owner="worker-2", lease_seconds=30)

    done = store.complete_work_item(tenant, leased[0].id, owner="worker-1", status="completed", result_ref="tri_1")
    assert done.status == "completed" and done.result_ref == "tri_1"
    # Completed items are terminal: a late/duplicate completion is a no-op.
    again = store.complete_work_item(tenant, leased[0].id, owner="worker-x", status="failed")
    assert again.status == "completed" and again.result_ref == "tri_1"
    with pytest.raises(ConcurrencyError):
        store.complete_work_item(tenant, leased[1].id, owner="worker-2", status="completed")

    store.complete_work_item(tenant, leased[1].id, owner="worker-1", status="failed", error_class="Boom", requeue=True)
    assert store.count_work_items(tenant, run_id="run_1", status="queued") == 1
    store.complete_work_item(tenant, others[0].id, owner="worker-2", status="dead_letter", error_class="Boom")
    assert store.requeue_work_items(tenant, run_id="run_1", statuses=["dead_letter"]) == 1
    assert store.cancel_work_items(tenant, run_id="run_1") == 3
    assert store.count_work_items(tenant, run_id="run_1", status="completed") == 1


def test_expired_lease_is_reclaimable(store) -> None:
    from datetime import timedelta

    from matraix.enterprise.domain.base import utcnow

    tenant = _tenant(store, "alpha")
    store.add_work_items([new_work_item(tenant_id=tenant, run_id="r", shard_id="s", seq=0, payload={}, idempotency_key="r:0")])
    now = utcnow()
    first = store.lease_work_items(tenant, owner="w1", limit=1, lease_seconds=10, now=now)
    assert len(first) == 1
    assert store.lease_work_items(tenant, owner="w2", limit=1, lease_seconds=10, now=now + timedelta(seconds=5)) == []
    # Lease expiry alone does not reopen a 'leased' row; requeue by the reaper does.
    store.requeue_work_items(tenant, run_id="r", statuses=["leased"])
    second = store.lease_work_items(tenant, owner="w2", limit=1, lease_seconds=10, now=now + timedelta(seconds=20))
    assert len(second) == 1 and second[0].lease_owner == "w2" and second[0].attempts == 2


def test_persona_snapshots_and_idempotency_keys(store) -> None:
    tenant = _tenant(store, "alpha")
    rows = [
        PersonaSnapshot(population_version_id="pov_1", seq=index, persona_ref=f"p{index}", path=f"p{index}.yaml", content_hash="h", weight=1.0)
        for index in range(3)
    ]
    assert store.add_persona_snapshots(tenant, rows) == 3
    assert store.count_persona_snapshots(tenant, "pov_1") == 3
    assert [row.seq for row in store.list_persona_snapshots(tenant, "pov_1", limit=2, offset=1)] == [1, 2]
    assert store.get_idempotent(tenant, "launch", "k1") is None
    store.put_idempotent(tenant, "launch", "k1", {"run_id": "run_1"})
    assert store.get_idempotent(tenant, "launch", "k1") == {"run_id": "run_1"}


def test_transaction_rolls_back_compound_operations(store) -> None:
    tenant = _tenant(store, "alpha")
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.put_record(Budget.create(tenant_id=tenant, name="a", limit_usd=1.0))
            store.put_record(Budget.create(tenant_id=tenant, name="b", limit_usd=1.0))
            raise RuntimeError("abort")
    assert store.count_records(tenant, EntityKind.BUDGET) == 0
    with store.transaction():
        store.put_record(Budget.create(tenant_id=tenant, name="c", limit_usd=1.0))
    assert store.count_records(tenant, EntityKind.BUDGET) == 1
