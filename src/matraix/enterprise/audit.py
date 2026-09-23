"""Append-only, hash-chained security audit stream.

Every privileged operation records an :class:`AuditEvent` through
:class:`AuditLog`. Rows are immutable: the store exposes append/list only, and
each row carries ``prev_hash``/``hash`` so tampering or deletion inside a
tenant's stream is detectable with :meth:`AuditLog.verify_chain`.

Categories cover the brief: authentication, resource creation/mutation,
experiment launch, policy decisions, approvals, denials, secret access
attempts, artifact access, administrative actions.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any

from matraix.enterprise.auth.principal import Principal
from matraix.enterprise.domain.base import coerce_tenant_id, utcnow
from matraix.enterprise.ids import EntityKind, new_id
from matraix.enterprise.records import AuditRow, RecordStore
from matraix.enterprise.secrets import redact

__all__ = ["AuditCategory", "AuditLog", "AuditOutcome"]


class AuditCategory(str, Enum):
    AUTHENTICATION = "authentication"
    RESOURCE_CREATE = "resource.create"
    RESOURCE_MUTATE = "resource.mutate"
    RESOURCE_DELETE = "resource.delete"
    EXPERIMENT_LAUNCH = "experiment.launch"
    POLICY_DECISION = "policy.decision"
    APPROVAL = "approval"
    DENIAL = "denial"
    SECRET_ACCESS = "secret.access"
    ARTIFACT_ACCESS = "artifact.access"
    ADMIN = "admin"
    WORKER = "worker"


class AuditOutcome(str, Enum):
    SUCCESS = "success"
    DENIED = "denied"
    FAILURE = "failure"


def _row_hash(prev_hash: str | None, payload: dict[str, Any]) -> str:
    material = json.dumps({"prev": prev_hash, **payload}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass
class AuditLog:
    """Tenant-partitioned audit writer/reader over a :class:`RecordStore`."""

    store: RecordStore
    redact_values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self._lock = threading.RLock()

    def record(
        self,
        *,
        tenant_id: str,
        principal: Principal | None,
        action: str,
        category: AuditCategory,
        outcome: AuditOutcome = AuditOutcome.SUCCESS,
        resource_type: str | None = None,
        resource_id: str | None = None,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditRow:
        tenant = coerce_tenant_id(tenant_id)
        safe_details = json.loads(redact(json.dumps(details or {}, default=str), self.redact_values))
        with self._lock:
            last = self.store.last_audit(tenant)
            seq = (last.seq + 1) if last is not None else 1
            prev_hash = last.hash if last is not None else None
            timestamp = utcnow()
            payload = {
                "tenant_id": tenant,
                "seq": seq,
                "timestamp": timestamp.isoformat(),
                "actor": principal.subject if principal else "system",
                "actor_kind": principal.kind.value if principal else "system",
                "action": action,
                "category": category.value,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "outcome": outcome.value,
                "request_id": request_id,
                "details": safe_details,
            }
            row = AuditRow(
                id=new_id(EntityKind.AUDIT_EVENT),
                tenant_id=tenant,
                seq=seq,
                timestamp=timestamp,
                actor=payload["actor"],
                actor_kind=payload["actor_kind"],
                action=action,
                category=category.value,
                resource_type=resource_type,
                resource_id=resource_id,
                outcome=outcome.value,
                request_id=request_id,
                details=safe_details,
                prev_hash=prev_hash,
                hash=_row_hash(prev_hash, payload),
            )
            return self.store.append_audit(row)

    def list(
        self,
        tenant_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
        category: str | None = None,
        action: str | None = None,
        resource_id: str | None = None,
    ) -> list[AuditRow]:
        return self.store.list_audit(
            coerce_tenant_id(tenant_id),
            limit=limit,
            offset=offset,
            category=category,
            action=action,
            resource_id=resource_id,
        )

    def verify_chain(self, tenant_id: str, *, limit: int = 100_000) -> dict[str, Any]:
        """Recompute hashes oldest→newest; report the first break if any."""
        rows = list(reversed(self.list(tenant_id, limit=limit)))
        prev_hash: str | None = None
        expected_seq = 1
        for row in rows:
            payload = {
                "tenant_id": row.tenant_id,
                "seq": row.seq,
                "timestamp": row.timestamp.isoformat(),
                "actor": row.actor,
                "actor_kind": row.actor_kind,
                "action": row.action,
                "category": row.category,
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "outcome": row.outcome,
                "request_id": row.request_id,
                "details": row.details,
            }
            if row.seq != expected_seq or row.prev_hash != prev_hash or _row_hash(prev_hash, payload) != row.hash:
                return {"ok": False, "checked": expected_seq - 1, "broken_at_seq": row.seq}
            prev_hash = row.hash
            expected_seq += 1
        return {"ok": True, "checked": len(rows), "broken_at_seq": None}
