"""Generic, tenant-partitioned, versioned record store.

All Phase 1+ entities (:mod:`matraix.enterprise.domain.models`) persist through
this contract. Isolation rules live here, not in HTTP handlers:

* every operation takes ``tenant_id`` and is executed against rows keyed by
  ``(tenant_id, kind, id)`` — a foreign tenant's record is indistinguishable
  from a missing one (``EntityNotFoundError``), by design;
* ``put_record`` enforces optimistic concurrency: an update must carry
  ``stored.version + 1`` (or an explicit ``expected_version``) or it raises
  :class:`ConcurrencyError`;
* dedicated append-only / high-volume tables (audit events, work items,
  persona snapshots, idempotency keys) share the same tenant partitioning.

Two implementations ship: :class:`InMemoryRecordStore` (tests / dev default)
and :class:`SqlRecordStoreBase`, a DB-API base parametrized by dialect that the
SQLite and PostgreSQL stores inherit.
"""

from __future__ import annotations

import copy
import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator, Protocol, Sequence, TypeVar

from matraix.enterprise.domain.base import EnterpriseRecord, coerce_tenant_id, utcnow
from matraix.enterprise.domain.models import RECORD_TYPES, PersonaSnapshot
from matraix.enterprise.errors import (
    EnterpriseError,
    EnterpriseSchemaError,
    EntityNotFoundError,
)
from matraix.enterprise.ids import EntityKind, new_id

R = TypeVar("R", bound=EnterpriseRecord)

__all__ = [
    "AuditRow",
    "ConcurrencyError",
    "InMemoryRecordStore",
    "RecordQuery",
    "RecordStore",
    "SqlRecordStoreBase",
    "WorkItemRow",
]


class ConcurrencyError(EnterpriseError):
    """Raised when an update does not match the stored record version."""


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True, slots=True)
class RecordQuery:
    """Filter / page spec for :meth:`RecordStore.list_records`."""

    organization_id: str | None = None
    parent_id: str | None = None
    status: str | None = None
    ids: tuple[str, ...] | None = None
    where: dict[str, Any] = field(default_factory=dict)
    limit: int | None = None
    offset: int = 0
    order_by: str = "created_at"
    descending: bool = False

    def matches(self, record: EnterpriseRecord) -> bool:
        if self.organization_id is not None and record.organization_id != self.organization_id:
            return False
        if self.parent_id is not None and record.parent_id() != self.parent_id:
            return False
        if self.status is not None and record.status != self.status:
            return False
        if self.ids is not None and record.id not in self.ids:
            return False
        if self.where:
            doc = record.model_dump(mode="json")
            for key, wanted in self.where.items():
                if doc.get(key) != wanted:
                    return False
        return True


@dataclass(frozen=True, slots=True)
class AuditRow:
    id: str
    tenant_id: str
    seq: int
    timestamp: datetime
    actor: str
    actor_kind: str
    action: str
    category: str
    resource_type: str | None
    resource_id: str | None
    outcome: str
    request_id: str | None
    details: dict[str, Any]
    prev_hash: str | None
    hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "seq": self.seq,
            "timestamp": _iso(self.timestamp),
            "actor": self.actor,
            "actor_kind": self.actor_kind,
            "action": self.action,
            "category": self.category,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "outcome": self.outcome,
            "request_id": self.request_id,
            "details": dict(self.details),
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }


@dataclass(slots=True)
class WorkItemRow:
    id: str
    tenant_id: str
    run_id: str
    shard_id: str
    seq: int
    status: str
    priority: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    attempts: int
    max_attempts: int
    idempotency_key: str
    payload: dict[str, Any]
    result_ref: str | None
    error_class: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    heartbeat_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "shard_id": self.shard_id,
            "seq": self.seq,
            "status": self.status,
            "priority": self.priority,
            "lease_owner": self.lease_owner,
            "lease_expires_at": _iso(self.lease_expires_at),
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "idempotency_key": self.idempotency_key,
            "payload": dict(self.payload),
            "result_ref": self.result_ref,
            "error_class": self.error_class,
            "error_message": self.error_message,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "completed_at": _iso(self.completed_at),
            "heartbeat_at": _iso(self.heartbeat_at),
        }


class RecordStore(Protocol):
    """Tenant-partitioned persistence for :class:`EnterpriseRecord` types."""

    # -- generic records -------------------------------------------------- #
    def put_record(
        self, record: R, *, expected_version: int | None = None
    ) -> R: ...

    def get_record(self, tenant_id: str, kind: EntityKind, record_id: str, record_type: type[R]) -> R: ...

    def list_records(
        self, tenant_id: str, kind: EntityKind, record_type: type[R], query: RecordQuery | None = None
    ) -> list[R]: ...

    def count_records(
        self, tenant_id: str, kind: EntityKind, query: RecordQuery | None = None
    ) -> int: ...

    def delete_record(self, tenant_id: str, kind: EntityKind, record_id: str) -> None: ...

    # -- append-only audit ------------------------------------------------ #
    def append_audit(self, row: AuditRow) -> AuditRow: ...

    def last_audit(self, tenant_id: str) -> AuditRow | None: ...

    def list_audit(
        self,
        tenant_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
        category: str | None = None,
        action: str | None = None,
        resource_id: str | None = None,
    ) -> list[AuditRow]: ...

    # -- work queue ------------------------------------------------------- #
    def add_work_items(self, items: Sequence[WorkItemRow]) -> int: ...

    def lease_work_items(
        self,
        tenant_id: str,
        *,
        owner: str,
        limit: int,
        lease_seconds: int,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> list[WorkItemRow]: ...

    def heartbeat_work_item(
        self, tenant_id: str, item_id: str, *, owner: str, lease_seconds: int, now: datetime | None = None
    ) -> bool: ...

    def complete_work_item(
        self,
        tenant_id: str,
        item_id: str,
        *,
        owner: str,
        status: str,
        result_ref: str | None = None,
        error_class: str | None = None,
        error_message: str | None = None,
        requeue: bool = False,
        now: datetime | None = None,
    ) -> WorkItemRow: ...

    def get_work_item(self, tenant_id: str, item_id: str) -> WorkItemRow: ...

    def list_work_items(
        self,
        tenant_id: str,
        *,
        run_id: str | None = None,
        status: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[WorkItemRow]: ...

    def count_work_items(self, tenant_id: str, *, run_id: str | None = None, status: str | None = None) -> int: ...

    def requeue_work_items(self, tenant_id: str, *, run_id: str, statuses: Sequence[str]) -> int: ...

    def cancel_work_items(self, tenant_id: str, *, run_id: str) -> int: ...

    # -- persona snapshots ------------------------------------------------ #
    def add_persona_snapshots(self, tenant_id: str, rows: Sequence[PersonaSnapshot]) -> int: ...

    def list_persona_snapshots(
        self, tenant_id: str, population_version_id: str, *, limit: int | None = None, offset: int = 0
    ) -> list[PersonaSnapshot]: ...

    def count_persona_snapshots(self, tenant_id: str, population_version_id: str) -> int: ...

    # -- idempotency ------------------------------------------------------ #
    def get_idempotent(self, tenant_id: str, scope: str, key: str) -> dict[str, Any] | None: ...

    def put_idempotent(self, tenant_id: str, scope: str, key: str, response: dict[str, Any]) -> None: ...

    # -- transactions ----------------------------------------------------- #
    def transaction(self) -> Any: ...


# --------------------------------------------------------------------------- #
# helpers shared by implementations
# --------------------------------------------------------------------------- #


def _check_put(record: EnterpriseRecord, stored: EnterpriseRecord | None, expected_version: int | None) -> None:
    if stored is None:
        if expected_version not in (None, 0):
            raise ConcurrencyError(
                f"{record.kind.value} {record.id}: expected version {expected_version} but record does not exist"
            )
        return
    if stored.tenant_id != record.tenant_id:
        raise EntityNotFoundError(f"unknown {record.kind.value} {record.id}")
    wanted = expected_version if expected_version is not None else record.version - 1
    if stored.version != wanted:
        raise ConcurrencyError(
            f"{record.kind.value} {record.id}: stored version {stored.version} != expected {wanted}"
        )
    if expected_version is not None and record.version <= stored.version:
        raise ConcurrencyError(
            f"{record.kind.value} {record.id}: new version must exceed {stored.version}"
        )


def _sort_records(items: list[R], query: RecordQuery) -> list[R]:
    key = query.order_by

    def sort_key(item: EnterpriseRecord) -> Any:
        value = getattr(item, key, None)
        if value is None:
            value = item.model_dump(mode="json").get(key)
        return (value is None, value if value is not None else "")

    items.sort(key=sort_key, reverse=query.descending)
    return items


def _page(items: list[Any], query: RecordQuery | None) -> list[Any]:
    if query is None:
        return items
    start = max(0, query.offset)
    end = start + query.limit if query.limit is not None else None
    return items[start:end]


# --------------------------------------------------------------------------- #
# In-memory implementation
# --------------------------------------------------------------------------- #


@dataclass
class _TenantRecords:
    records: dict[EntityKind, dict[str, EnterpriseRecord]] = field(default_factory=dict)
    audit: list[AuditRow] = field(default_factory=list)
    work_items: dict[str, WorkItemRow] = field(default_factory=dict)
    snapshots: dict[str, list[PersonaSnapshot]] = field(default_factory=dict)
    idempotency: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)


class InMemoryRecordStore:
    """Process-local :class:`RecordStore`. Deterministic and test-friendly."""

    def __init__(self) -> None:
        self._record_tenants: dict[str, _TenantRecords] = {}
        self._lock = threading.RLock()
        self._tx_depth = 0

    def _record_bucket(self, tenant_id: str) -> _TenantRecords:
        key = coerce_tenant_id(tenant_id)
        return self._record_tenants.setdefault(key, _TenantRecords())

    # records
    def put_record(self, record: R, *, expected_version: int | None = None) -> R:
        with self._lock:
            bucket = self._record_bucket(record.tenant_id)
            table = bucket.records.setdefault(record.kind, {})
            _check_put(record, table.get(record.id), expected_version)
            table[record.id] = record
            return record

    def get_record(self, tenant_id: str, kind: EntityKind, record_id: str, record_type: type[R]) -> R:
        with self._lock:
            table = self._record_bucket(tenant_id).records.get(kind, {})
            item = table.get(record_id)
            if item is None or item.tenant_id != coerce_tenant_id(tenant_id):
                raise EntityNotFoundError(f"unknown {kind.value} {record_id}")
            if not isinstance(item, record_type):
                item = record_type.from_document(item.to_document())
            return item

    def list_records(
        self, tenant_id: str, kind: EntityKind, record_type: type[R], query: RecordQuery | None = None
    ) -> list[R]:
        with self._lock:
            table = self._record_bucket(tenant_id).records.get(kind, {})
            items: list[R] = [item for item in table.values()]  # type: ignore[misc]
            if query is not None:
                items = [item for item in items if query.matches(item)]
                items = _sort_records(items, query)
            else:
                items = _sort_records(items, RecordQuery())
            return _page(items, query)

    def count_records(self, tenant_id: str, kind: EntityKind, query: RecordQuery | None = None) -> int:
        with self._lock:
            table = self._record_bucket(tenant_id).records.get(kind, {})
            if query is None:
                return len(table)
            return sum(1 for item in table.values() if query.matches(item))

    def delete_record(self, tenant_id: str, kind: EntityKind, record_id: str) -> None:
        with self._lock:
            table = self._record_bucket(tenant_id).records.get(kind, {})
            if record_id not in table:
                raise EntityNotFoundError(f"unknown {kind.value} {record_id}")
            del table[record_id]

    # audit
    def append_audit(self, row: AuditRow) -> AuditRow:
        with self._lock:
            self._record_bucket(row.tenant_id).audit.append(row)
            return row

    def last_audit(self, tenant_id: str) -> AuditRow | None:
        with self._lock:
            audit = self._record_bucket(tenant_id).audit
            return audit[-1] if audit else None

    def list_audit(
        self,
        tenant_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
        category: str | None = None,
        action: str | None = None,
        resource_id: str | None = None,
    ) -> list[AuditRow]:
        with self._lock:
            rows = list(self._record_bucket(tenant_id).audit)
            if category:
                rows = [row for row in rows if row.category == category]
            if action:
                rows = [row for row in rows if row.action == action]
            if resource_id:
                rows = [row for row in rows if row.resource_id == resource_id]
            rows.sort(key=lambda row: row.seq, reverse=True)
            return rows[offset : offset + limit]

    # work items
    def add_work_items(self, items: Sequence[WorkItemRow]) -> int:
        added = 0
        with self._lock:
            for item in items:
                bucket = self._record_bucket(item.tenant_id)
                if any(existing.idempotency_key == item.idempotency_key for existing in bucket.work_items.values()):
                    continue
                bucket.work_items[item.id] = copy.deepcopy(item)
                added += 1
        return added

    def lease_work_items(
        self,
        tenant_id: str,
        *,
        owner: str,
        limit: int,
        lease_seconds: int,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> list[WorkItemRow]:
        now = now or utcnow()
        leased: list[WorkItemRow] = []
        with self._lock:
            candidates = [
                item
                for item in self._record_bucket(tenant_id).work_items.values()
                if item.status == "queued"
                and (run_id is None or item.run_id == run_id)
                and (item.lease_owner is None or item.lease_expires_at is None or item.lease_expires_at <= now)
            ]
            candidates.sort(key=lambda item: (-item.priority, item.seq, item.created_at))
            for item in candidates[: max(0, limit)]:
                item.status = "leased"
                item.lease_owner = owner
                item.lease_expires_at = now + timedelta(seconds=lease_seconds)
                item.attempts += 1
                item.updated_at = now
                item.heartbeat_at = now
                leased.append(copy.deepcopy(item))
        return leased

    def heartbeat_work_item(
        self, tenant_id: str, item_id: str, *, owner: str, lease_seconds: int, now: datetime | None = None
    ) -> bool:
        now = now or utcnow()
        with self._lock:
            item = self._record_bucket(tenant_id).work_items.get(item_id)
            if item is None or item.lease_owner != owner or item.status != "leased":
                return False
            item.lease_expires_at = now + timedelta(seconds=lease_seconds)
            item.heartbeat_at = now
            item.updated_at = now
            return True

    def complete_work_item(
        self,
        tenant_id: str,
        item_id: str,
        *,
        owner: str,
        status: str,
        result_ref: str | None = None,
        error_class: str | None = None,
        error_message: str | None = None,
        requeue: bool = False,
        now: datetime | None = None,
    ) -> WorkItemRow:
        now = now or utcnow()
        with self._lock:
            item = self._record_bucket(tenant_id).work_items.get(item_id)
            if item is None:
                raise EntityNotFoundError(f"unknown work item {item_id}")
            if item.status == "completed":
                # Idempotent: a completed item never changes again.
                return copy.deepcopy(item)
            if item.lease_owner != owner:
                raise ConcurrencyError(f"work item {item_id} is leased by {item.lease_owner!r}, not {owner!r}")
            item.status = "queued" if requeue else status
            item.lease_owner = None
            item.lease_expires_at = None
            item.result_ref = result_ref if result_ref is not None else item.result_ref
            item.error_class = error_class
            item.error_message = error_message
            item.updated_at = now
            item.completed_at = now if status == "completed" and not requeue else None
            return copy.deepcopy(item)

    def get_work_item(self, tenant_id: str, item_id: str) -> WorkItemRow:
        with self._lock:
            item = self._record_bucket(tenant_id).work_items.get(item_id)
            if item is None:
                raise EntityNotFoundError(f"unknown work item {item_id}")
            return copy.deepcopy(item)

    def list_work_items(
        self,
        tenant_id: str,
        *,
        run_id: str | None = None,
        status: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[WorkItemRow]:
        with self._lock:
            items = [
                copy.deepcopy(item)
                for item in self._record_bucket(tenant_id).work_items.values()
                if (run_id is None or item.run_id == run_id) and (status is None or item.status == status)
            ]
            items.sort(key=lambda item: (item.run_id, item.seq))
            return items[offset : offset + limit]

    def count_work_items(self, tenant_id: str, *, run_id: str | None = None, status: str | None = None) -> int:
        with self._lock:
            return sum(
                1
                for item in self._record_bucket(tenant_id).work_items.values()
                if (run_id is None or item.run_id == run_id) and (status is None or item.status == status)
            )

    def requeue_work_items(self, tenant_id: str, *, run_id: str, statuses: Sequence[str]) -> int:
        wanted = set(statuses)
        count = 0
        now = utcnow()
        with self._lock:
            for item in self._record_bucket(tenant_id).work_items.values():
                if item.run_id == run_id and item.status in wanted:
                    item.status = "queued"
                    item.lease_owner = None
                    item.lease_expires_at = None
                    item.attempts = 0 if item.status == "dead_letter" else item.attempts
                    item.error_class = None
                    item.error_message = None
                    item.updated_at = now
                    count += 1
        return count

    def cancel_work_items(self, tenant_id: str, *, run_id: str) -> int:
        count = 0
        now = utcnow()
        with self._lock:
            for item in self._record_bucket(tenant_id).work_items.values():
                if item.run_id == run_id and item.status in {"queued", "leased"}:
                    item.status = "cancelled"
                    item.lease_owner = None
                    item.lease_expires_at = None
                    item.updated_at = now
                    count += 1
        return count

    # snapshots
    def add_persona_snapshots(self, tenant_id: str, rows: Sequence[PersonaSnapshot]) -> int:
        with self._lock:
            bucket = self._record_bucket(tenant_id)
            for row in rows:
                bucket.snapshots.setdefault(row.population_version_id, []).append(row)
            return len(rows)

    def list_persona_snapshots(
        self, tenant_id: str, population_version_id: str, *, limit: int | None = None, offset: int = 0
    ) -> list[PersonaSnapshot]:
        with self._lock:
            rows = sorted(
                self._record_bucket(tenant_id).snapshots.get(population_version_id, []),
                key=lambda row: row.seq,
            )
            end = offset + limit if limit is not None else None
            return rows[offset:end]

    def count_persona_snapshots(self, tenant_id: str, population_version_id: str) -> int:
        with self._lock:
            return len(self._record_bucket(tenant_id).snapshots.get(population_version_id, []))

    # idempotency
    def get_idempotent(self, tenant_id: str, scope: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            stored = self._record_bucket(tenant_id).idempotency.get((scope, key))
            return copy.deepcopy(stored) if stored is not None else None

    def put_idempotent(self, tenant_id: str, scope: str, key: str, response: dict[str, Any]) -> None:
        with self._lock:
            self._record_bucket(tenant_id).idempotency[(scope, key)] = copy.deepcopy(response)

    # transactions
    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Snapshot/restore transaction. Nested calls join the outer scope."""
        with self._lock:
            if self._tx_depth > 0:
                self._tx_depth += 1
                try:
                    yield
                finally:
                    self._tx_depth -= 1
                return
            snapshot = copy.deepcopy(self._record_tenants)
            self._tx_depth = 1
            try:
                yield
            except BaseException:
                self._record_tenants = snapshot
                raise
            finally:
                self._tx_depth = 0


# --------------------------------------------------------------------------- #
# SQL implementation (dialect-parametrized)
# --------------------------------------------------------------------------- #


class SqlRecordStoreBase:
    """DB-API record store. Subclasses provide ``_conn``, ``_lock``, ``dialect``.

    SQL is written with ``?`` placeholders and rewritten for ``%s`` dialects.
    Timestamps are ISO-8601 text in both dialects for portability.
    """

    dialect: str = "sqlite"
    _conn: Any
    _lock: threading.RLock

    def _sql(self, text: str) -> str:
        if self.dialect == "postgres":
            return text.replace("?", "%s")
        return text

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        cursor = self._conn.cursor() if self.dialect == "postgres" else self._conn
        return cursor.execute(self._sql(sql), tuple(params))

    def _fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[Any]:
        cursor = self._execute(sql, params)
        return list(cursor.fetchall())

    def _fetchone(self, sql: str, params: Sequence[Any] = ()) -> Any:
        cursor = self._execute(sql, params)
        return cursor.fetchone()

    def _commit(self) -> None:
        if getattr(self, "_tx_depth", 0) == 0:
            self._conn.commit()

    def _row(self, row: Any) -> dict[str, Any]:
        if isinstance(row, dict):
            return row
        keys = row.keys() if hasattr(row, "keys") else None
        if keys is not None:
            return {key: row[key] for key in keys}
        raise TypeError("row factory must expose keys()")

    # records ------------------------------------------------------------- #
    def _load_record(self, tenant_id: str, kind: EntityKind, record_id: str) -> EnterpriseRecord | None:
        row = self._fetchone(
            "SELECT body FROM enterprise_records WHERE tenant_id = ? AND kind = ? AND id = ?",
            (coerce_tenant_id(tenant_id), kind.value, record_id),
        )
        if row is None:
            return None
        doc = json.loads(self._row(row)["body"])
        record_type = RECORD_TYPES.get(kind)
        if record_type is None:
            raise EnterpriseSchemaError(f"no record type registered for {kind.value}")
        return record_type.from_document(doc)

    def put_record(self, record: R, *, expected_version: int | None = None) -> R:
        with self._lock:
            stored = self._load_record(record.tenant_id, record.kind, record.id)
            _check_put(record, stored, expected_version)
            body = _dumps(record.to_document())
            params = (
                record.tenant_id,
                record.kind.value,
                record.id,
                record.organization_id,
                record.parent_id(),
                record.status,
                record.version,
                _iso(record.created_at),
                _iso(record.updated_at),
                record.created_by,
                body,
            )
            if stored is None:
                self._execute(
                    """
                    INSERT INTO enterprise_records(
                        tenant_id, kind, id, organization_id, parent_id, status, version,
                        created_at, updated_at, created_by, body
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    params,
                )
            else:
                self._execute(
                    """
                    UPDATE enterprise_records SET organization_id = ?, parent_id = ?, status = ?,
                        version = ?, created_at = ?, updated_at = ?, created_by = ?, body = ?
                    WHERE tenant_id = ? AND kind = ? AND id = ? AND version = ?
                    """,
                    (
                        record.organization_id,
                        record.parent_id(),
                        record.status,
                        record.version,
                        _iso(record.created_at),
                        _iso(record.updated_at),
                        record.created_by,
                        body,
                        record.tenant_id,
                        record.kind.value,
                        record.id,
                        stored.version,
                    ),
                )
            self._commit()
            return record

    def get_record(self, tenant_id: str, kind: EntityKind, record_id: str, record_type: type[R]) -> R:
        with self._lock:
            item = self._load_record(tenant_id, kind, record_id)
            if item is None:
                raise EntityNotFoundError(f"unknown {kind.value} {record_id}")
            if not isinstance(item, record_type):
                item = record_type.from_document(item.to_document())
            return item

    def _query_sql(self, tenant_id: str, kind: EntityKind, query: RecordQuery | None) -> tuple[str, list[Any]]:
        clauses = ["tenant_id = ?", "kind = ?"]
        params: list[Any] = [coerce_tenant_id(tenant_id), kind.value]
        if query is not None:
            if query.organization_id is not None:
                clauses.append("organization_id = ?")
                params.append(query.organization_id)
            if query.parent_id is not None:
                clauses.append("parent_id = ?")
                params.append(query.parent_id)
            if query.status is not None:
                clauses.append("status = ?")
                params.append(query.status)
            if query.ids is not None:
                if not query.ids:
                    clauses.append("1 = 0")
                else:
                    clauses.append("id IN (" + ",".join("?" for _ in query.ids) + ")")
                    params.extend(query.ids)
        return " WHERE " + " AND ".join(clauses), params

    def list_records(
        self, tenant_id: str, kind: EntityKind, record_type: type[R], query: RecordQuery | None = None
    ) -> list[R]:
        with self._lock:
            where, params = self._query_sql(tenant_id, kind, query)
            order_col = "created_at"
            if query is not None and query.order_by in {"created_at", "updated_at", "status", "version", "id"}:
                order_col = query.order_by
            direction = "DESC" if query is not None and query.descending else "ASC"
            rows = self._fetchall(
                f"SELECT body FROM enterprise_records{where} ORDER BY {order_col} {direction}, id ASC",
                params,
            )
            items: list[R] = [record_type.from_document(json.loads(self._row(row)["body"])) for row in rows]
            if query is not None and (query.where or query.order_by not in {"created_at", "updated_at", "status", "version", "id"}):
                items = [item for item in items if query.matches(item)]
                items = _sort_records(items, query)
            return _page(items, query)

    def count_records(self, tenant_id: str, kind: EntityKind, query: RecordQuery | None = None) -> int:
        with self._lock:
            if query is not None and query.where:
                record_type = RECORD_TYPES[kind]
                return len(
                    self.list_records(
                        tenant_id, kind, record_type, RecordQuery(
                            organization_id=query.organization_id,
                            parent_id=query.parent_id,
                            status=query.status,
                            ids=query.ids,
                            where=query.where,
                        )
                    )
                )
            where, params = self._query_sql(tenant_id, kind, query)
            row = self._fetchone(f"SELECT COUNT(*) AS n FROM enterprise_records{where}", params)
            return int(self._row(row)["n"])

    def delete_record(self, tenant_id: str, kind: EntityKind, record_id: str) -> None:
        with self._lock:
            if self._load_record(tenant_id, kind, record_id) is None:
                raise EntityNotFoundError(f"unknown {kind.value} {record_id}")
            self._execute(
                "DELETE FROM enterprise_records WHERE tenant_id = ? AND kind = ? AND id = ?",
                (coerce_tenant_id(tenant_id), kind.value, record_id),
            )
            self._commit()

    # audit --------------------------------------------------------------- #
    def _audit_from_row(self, row: Any) -> AuditRow:
        data = self._row(row)
        return AuditRow(
            id=data["id"],
            tenant_id=data["tenant_id"],
            seq=int(data["seq"]),
            timestamp=_parse_iso(data["ts"]) or utcnow(),
            actor=data["actor"],
            actor_kind=data["actor_kind"],
            action=data["action"],
            category=data["category"],
            resource_type=data["resource_type"],
            resource_id=data["resource_id"],
            outcome=data["outcome"],
            request_id=data["request_id"],
            details=json.loads(data["details_json"] or "{}"),
            prev_hash=data["prev_hash"],
            hash=data["hash"],
        )

    def append_audit(self, row: AuditRow) -> AuditRow:
        with self._lock:
            self._execute(
                """
                INSERT INTO audit_events(
                    tenant_id, id, seq, ts, actor, actor_kind, action, category,
                    resource_type, resource_id, outcome, request_id, details_json, prev_hash, hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.tenant_id,
                    row.id,
                    row.seq,
                    _iso(row.timestamp),
                    row.actor,
                    row.actor_kind,
                    row.action,
                    row.category,
                    row.resource_type,
                    row.resource_id,
                    row.outcome,
                    row.request_id,
                    _dumps(row.details),
                    row.prev_hash,
                    row.hash,
                ),
            )
            self._commit()
            return row

    def last_audit(self, tenant_id: str) -> AuditRow | None:
        with self._lock:
            row = self._fetchone(
                "SELECT * FROM audit_events WHERE tenant_id = ? ORDER BY seq DESC LIMIT 1",
                (coerce_tenant_id(tenant_id),),
            )
            return self._audit_from_row(row) if row is not None else None

    def list_audit(
        self,
        tenant_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
        category: str | None = None,
        action: str | None = None,
        resource_id: str | None = None,
    ) -> list[AuditRow]:
        with self._lock:
            clauses = ["tenant_id = ?"]
            params: list[Any] = [coerce_tenant_id(tenant_id)]
            if category:
                clauses.append("category = ?")
                params.append(category)
            if action:
                clauses.append("action = ?")
                params.append(action)
            if resource_id:
                clauses.append("resource_id = ?")
                params.append(resource_id)
            params.extend([int(limit), int(offset)])
            rows = self._fetchall(
                "SELECT * FROM audit_events WHERE " + " AND ".join(clauses) + " ORDER BY seq DESC LIMIT ? OFFSET ?",
                params,
            )
            return [self._audit_from_row(row) for row in rows]

    # work items ---------------------------------------------------------- #
    def _work_from_row(self, row: Any) -> WorkItemRow:
        data = self._row(row)
        return WorkItemRow(
            id=data["id"],
            tenant_id=data["tenant_id"],
            run_id=data["run_id"],
            shard_id=data["shard_id"],
            seq=int(data["seq"]),
            status=data["status"],
            priority=int(data["priority"]),
            lease_owner=data["lease_owner"],
            lease_expires_at=_parse_iso(data["lease_expires_at"]),
            attempts=int(data["attempts"]),
            max_attempts=int(data["max_attempts"]),
            idempotency_key=data["idempotency_key"],
            payload=json.loads(data["payload_json"] or "{}"),
            result_ref=data["result_ref"],
            error_class=data["error_class"],
            error_message=data["error_message"],
            created_at=_parse_iso(data["created_at"]) or utcnow(),
            updated_at=_parse_iso(data["updated_at"]) or utcnow(),
            completed_at=_parse_iso(data["completed_at"]),
            heartbeat_at=_parse_iso(data["heartbeat_at"]),
        )

    def add_work_items(self, items: Sequence[WorkItemRow]) -> int:
        added = 0
        with self._lock:
            for item in items:
                cursor = self._execute(
                    """
                    INSERT INTO work_items(
                        tenant_id, id, run_id, shard_id, seq, status, priority, lease_owner,
                        lease_expires_at, attempts, max_attempts, idempotency_key, payload_json,
                        result_ref, error_class, error_message, created_at, updated_at, completed_at, heartbeat_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, idempotency_key) DO NOTHING
                    """,
                    (
                        item.tenant_id,
                        item.id,
                        item.run_id,
                        item.shard_id,
                        item.seq,
                        item.status,
                        item.priority,
                        item.lease_owner,
                        _iso(item.lease_expires_at),
                        item.attempts,
                        item.max_attempts,
                        item.idempotency_key,
                        _dumps(item.payload),
                        item.result_ref,
                        item.error_class,
                        item.error_message,
                        _iso(item.created_at),
                        _iso(item.updated_at),
                        _iso(item.completed_at),
                        _iso(item.heartbeat_at),
                    ),
                )
                added += int(cursor.rowcount or 0) if cursor.rowcount is not None and cursor.rowcount >= 0 else 1
            self._commit()
        return added

    def lease_work_items(
        self,
        tenant_id: str,
        *,
        owner: str,
        limit: int,
        lease_seconds: int,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> list[WorkItemRow]:
        now = now or utcnow()
        now_iso = _iso(now)
        expires = _iso(now + timedelta(seconds=lease_seconds))
        tenant = coerce_tenant_id(tenant_id)
        with self._lock:
            clauses = [
                "tenant_id = ?",
                "status = 'queued'",
                "(lease_owner IS NULL OR lease_expires_at IS NULL OR lease_expires_at <= ?)",
            ]
            params: list[Any] = [tenant, now_iso]
            if run_id is not None:
                clauses.append("run_id = ?")
                params.append(run_id)
            select = (
                "SELECT id FROM work_items WHERE " + " AND ".join(clauses)
                + " ORDER BY priority DESC, seq ASC, created_at ASC LIMIT ?"
            )
            params.append(int(limit))
            if self.dialect == "postgres":
                select += " FOR UPDATE SKIP LOCKED"
            ids = [self._row(row)["id"] for row in self._fetchall(select, params)]
            if not ids:
                self._commit()
                return []
            placeholders = ",".join("?" for _ in ids)
            self._execute(
                f"""
                UPDATE work_items SET status = 'leased', lease_owner = ?, lease_expires_at = ?,
                    attempts = attempts + 1, updated_at = ?, heartbeat_at = ?
                WHERE tenant_id = ? AND status = 'queued' AND id IN ({placeholders})
                """,
                [owner, expires, now_iso, now_iso, tenant, *ids],
            )
            rows = self._fetchall(
                f"SELECT * FROM work_items WHERE tenant_id = ? AND lease_owner = ? AND id IN ({placeholders})",
                [tenant, owner, *ids],
            )
            self._commit()
            leased = [self._work_from_row(row) for row in rows]
            leased.sort(key=lambda item: (-item.priority, item.seq))
            return leased

    def heartbeat_work_item(
        self, tenant_id: str, item_id: str, *, owner: str, lease_seconds: int, now: datetime | None = None
    ) -> bool:
        now = now or utcnow()
        with self._lock:
            cursor = self._execute(
                """
                UPDATE work_items SET lease_expires_at = ?, heartbeat_at = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ? AND lease_owner = ? AND status = 'leased'
                """,
                (
                    _iso(now + timedelta(seconds=lease_seconds)),
                    _iso(now),
                    _iso(now),
                    coerce_tenant_id(tenant_id),
                    item_id,
                    owner,
                ),
            )
            self._commit()
            return bool(cursor.rowcount)

    def complete_work_item(
        self,
        tenant_id: str,
        item_id: str,
        *,
        owner: str,
        status: str,
        result_ref: str | None = None,
        error_class: str | None = None,
        error_message: str | None = None,
        requeue: bool = False,
        now: datetime | None = None,
    ) -> WorkItemRow:
        now = now or utcnow()
        tenant = coerce_tenant_id(tenant_id)
        with self._lock:
            current = self.get_work_item(tenant, item_id)
            if current.status == "completed":
                return current
            if current.lease_owner != owner:
                raise ConcurrencyError(
                    f"work item {item_id} is leased by {current.lease_owner!r}, not {owner!r}"
                )
            new_status = "queued" if requeue else status
            completed_at = _iso(now) if (status == "completed" and not requeue) else None
            self._execute(
                """
                UPDATE work_items SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                    result_ref = COALESCE(?, result_ref), error_class = ?, error_message = ?,
                    updated_at = ?, completed_at = ?
                WHERE tenant_id = ? AND id = ? AND lease_owner = ?
                """,
                (new_status, result_ref, error_class, error_message, _iso(now), completed_at, tenant, item_id, owner),
            )
            self._commit()
            return self.get_work_item(tenant, item_id)

    def get_work_item(self, tenant_id: str, item_id: str) -> WorkItemRow:
        with self._lock:
            row = self._fetchone(
                "SELECT * FROM work_items WHERE tenant_id = ? AND id = ?",
                (coerce_tenant_id(tenant_id), item_id),
            )
            if row is None:
                raise EntityNotFoundError(f"unknown work item {item_id}")
            return self._work_from_row(row)

    def list_work_items(
        self,
        tenant_id: str,
        *,
        run_id: str | None = None,
        status: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[WorkItemRow]:
        with self._lock:
            clauses = ["tenant_id = ?"]
            params: list[Any] = [coerce_tenant_id(tenant_id)]
            if run_id is not None:
                clauses.append("run_id = ?")
                params.append(run_id)
            if status is not None:
                clauses.append("status = ?")
                params.append(status)
            params.extend([int(limit), int(offset)])
            rows = self._fetchall(
                "SELECT * FROM work_items WHERE " + " AND ".join(clauses) + " ORDER BY run_id, seq LIMIT ? OFFSET ?",
                params,
            )
            return [self._work_from_row(row) for row in rows]

    def count_work_items(self, tenant_id: str, *, run_id: str | None = None, status: str | None = None) -> int:
        with self._lock:
            clauses = ["tenant_id = ?"]
            params: list[Any] = [coerce_tenant_id(tenant_id)]
            if run_id is not None:
                clauses.append("run_id = ?")
                params.append(run_id)
            if status is not None:
                clauses.append("status = ?")
                params.append(status)
            row = self._fetchone("SELECT COUNT(*) AS n FROM work_items WHERE " + " AND ".join(clauses), params)
            return int(self._row(row)["n"])

    def requeue_work_items(self, tenant_id: str, *, run_id: str, statuses: Sequence[str]) -> int:
        if not statuses:
            return 0
        with self._lock:
            placeholders = ",".join("?" for _ in statuses)
            cursor = self._execute(
                f"""
                UPDATE work_items SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL,
                    error_class = NULL, error_message = NULL, updated_at = ?,
                    attempts = CASE WHEN status = 'dead_letter' THEN 0 ELSE attempts END
                WHERE tenant_id = ? AND run_id = ? AND status IN ({placeholders})
                """,
                [_iso(utcnow()), coerce_tenant_id(tenant_id), run_id, *statuses],
            )
            self._commit()
            return int(cursor.rowcount or 0)

    def cancel_work_items(self, tenant_id: str, *, run_id: str) -> int:
        with self._lock:
            cursor = self._execute(
                """
                UPDATE work_items SET status = 'cancelled', lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE tenant_id = ? AND run_id = ? AND status IN ('queued', 'leased')
                """,
                (_iso(utcnow()), coerce_tenant_id(tenant_id), run_id),
            )
            self._commit()
            return int(cursor.rowcount or 0)

    # persona snapshots --------------------------------------------------- #
    def add_persona_snapshots(self, tenant_id: str, rows: Sequence[PersonaSnapshot]) -> int:
        tenant = coerce_tenant_id(tenant_id)
        with self._lock:
            params = [
                (
                    tenant,
                    row.population_version_id,
                    row.seq,
                    row.persona_ref,
                    row.path,
                    row.content_hash,
                    row.weight,
                    row.segment,
                    row.source,
                    _dumps(row.dimensions_summary),
                )
                for row in rows
            ]
            sql = self._sql(
                """
                INSERT INTO persona_snapshots(
                    tenant_id, population_version_id, seq, persona_ref, path, content_hash,
                    weight, segment, source, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, population_version_id, seq) DO NOTHING
                """
            )
            cursor = self._conn.cursor() if self.dialect == "postgres" else self._conn
            cursor.executemany(sql, params)
            self._commit()
            return len(params)

    def list_persona_snapshots(
        self, tenant_id: str, population_version_id: str, *, limit: int | None = None, offset: int = 0
    ) -> list[PersonaSnapshot]:
        with self._lock:
            sql = (
                "SELECT * FROM persona_snapshots WHERE tenant_id = ? AND population_version_id = ? ORDER BY seq"
            )
            params: list[Any] = [coerce_tenant_id(tenant_id), population_version_id]
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                params.extend([int(limit), int(offset)])
            elif offset:
                sql += " LIMIT -1 OFFSET ?" if self.dialect == "sqlite" else " OFFSET ?"
                params.append(int(offset))
            rows = self._fetchall(sql, params)
            out: list[PersonaSnapshot] = []
            for raw in rows:
                data = self._row(raw)
                out.append(
                    PersonaSnapshot(
                        population_version_id=data["population_version_id"],
                        seq=int(data["seq"]),
                        persona_ref=data["persona_ref"],
                        path=data["path"],
                        content_hash=data["content_hash"],
                        weight=float(data["weight"]),
                        segment=data["segment"],
                        source=data["source"] or "",
                        dimensions_summary=json.loads(data["summary_json"] or "{}"),
                    )
                )
            return out

    def count_persona_snapshots(self, tenant_id: str, population_version_id: str) -> int:
        with self._lock:
            row = self._fetchone(
                "SELECT COUNT(*) AS n FROM persona_snapshots WHERE tenant_id = ? AND population_version_id = ?",
                (coerce_tenant_id(tenant_id), population_version_id),
            )
            return int(self._row(row)["n"])

    # idempotency --------------------------------------------------------- #
    def get_idempotent(self, tenant_id: str, scope: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._fetchone(
                "SELECT response_json FROM idempotency_keys WHERE tenant_id = ? AND scope = ? AND key = ?",
                (coerce_tenant_id(tenant_id), scope, key),
            )
            return json.loads(self._row(row)["response_json"]) if row is not None else None

    def put_idempotent(self, tenant_id: str, scope: str, key: str, response: dict[str, Any]) -> None:
        with self._lock:
            self._execute(
                """
                INSERT INTO idempotency_keys(tenant_id, scope, key, response_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, scope, key) DO UPDATE SET response_json = excluded.response_json
                """,
                (coerce_tenant_id(tenant_id), scope, key, _dumps(response), _iso(utcnow())),
            )
            self._commit()

    # transactions -------------------------------------------------------- #
    _tx_depth: int = 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Compound-operation boundary: commit on success, rollback on error."""
        with self._lock:
            if self._tx_depth > 0:
                self._tx_depth += 1
                try:
                    yield
                finally:
                    self._tx_depth -= 1
                return
            self._tx_depth = 1
            try:
                yield
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()
            finally:
                self._tx_depth = 0


def new_work_item(
    *,
    tenant_id: str,
    run_id: str,
    shard_id: str,
    seq: int,
    payload: dict[str, Any],
    idempotency_key: str,
    priority: int = 0,
    max_attempts: int = 3,
    now: datetime | None = None,
) -> WorkItemRow:
    now = now or utcnow()
    return WorkItemRow(
        id=new_id(EntityKind.WORK_ITEM),
        tenant_id=coerce_tenant_id(tenant_id),
        run_id=run_id,
        shard_id=shard_id,
        seq=seq,
        status="queued",
        priority=priority,
        lease_owner=None,
        lease_expires_at=None,
        attempts=0,
        max_attempts=max_attempts,
        idempotency_key=idempotency_key,
        payload=dict(payload),
        result_ref=None,
        error_class=None,
        error_message=None,
        created_at=now,
        updated_at=now,
        completed_at=None,
    )


StoreFactory = Callable[[], RecordStore]
