"""Append-only audit log, separable from telemetry and metrics.

Audit records are not Phase 6 artifacts. The store exposes ``append`` and
``list`` / ``export`` only — no update or delete API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from matraix.enterprise.errors import AppendOnlyAuditError
from matraix.enterprise.ids import TenantId


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: str
    actor: str
    action: str
    resource: str
    result: str
    tenant_id: TenantId | None = None
    created_at: str = field(default_factory=_utcnow)
    policy: str | None = None
    trace_id: str | None = None
    ip: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id.value if self.tenant_id else None,
            "actor": self.actor,
            "action": self.action,
            "resource": self.resource,
            "result": self.result,
            "created_at": self.created_at,
            "policy": self.policy,
            "trace_id": self.trace_id,
            "ip": self.ip,
            "details": dict(self.details),
        }


def new_audit_event(
    *,
    actor: str,
    action: str,
    resource: str,
    result: str,
    tenant_id: TenantId | None = None,
    policy: str | None = None,
    trace_id: str | None = None,
    ip: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    return AuditEvent(
        id=f"aud_{uuid4().hex}",
        actor=actor,
        action=action,
        resource=resource,
        result=result,
        tenant_id=tenant_id,
        policy=policy,
        trace_id=trace_id,
        ip=ip,
        details=dict(details or {}),
    )


def refuse_audit_mutation() -> None:
    raise AppendOnlyAuditError("audit log is append-only")
