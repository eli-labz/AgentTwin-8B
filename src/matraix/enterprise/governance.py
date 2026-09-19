"""Governance review records for ingestion, retention, and model exposure."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from matraix.enterprise.errors import EnterpriseSchemaError
from matraix.enterprise.ids import TenantId


class ReviewKind(str, Enum):
    INGESTION = "ingestion"
    RETENTION = "retention"
    MODEL_PROVIDER_EXPOSURE = "model_provider_exposure"


class ReviewStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class GovernanceReview:
    tenant_id: TenantId
    kind: ReviewKind
    title: str
    id: str = ""
    status: ReviewStatus = ReviewStatus.DRAFT
    notes: str = ""
    reviewer: str | None = None
    sign_off: bool = False
    created_at: str = field(default_factory=_utcnow)
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        kind = self.kind if isinstance(self.kind, ReviewKind) else ReviewKind(self.kind)
        status = (
            self.status
            if isinstance(self.status, ReviewStatus)
            else ReviewStatus(self.status)
        )
        title = str(self.title or "").strip()
        if not title:
            raise EnterpriseSchemaError("governance review title is required")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "notes", str(self.notes or ""))
        object.__setattr__(self, "details", dict(self.details or {}))
        if not self.id:
            object.__setattr__(self, "id", f"gov_{uuid4().hex}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id.value,
            "kind": self.kind.value,
            "status": self.status.value,
            "title": self.title,
            "notes": self.notes,
            "reviewer": self.reviewer,
            "sign_off": self.sign_off,
            "created_at": self.created_at,
            "details": dict(self.details),
        }
