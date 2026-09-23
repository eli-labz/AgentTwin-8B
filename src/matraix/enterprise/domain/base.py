"""Common envelope for persisted enterprise records.

Every record added from Phase 1 onward carries the same envelope so the
generic record store, the API, and the audit stream can treat them uniformly:

``id``, ``tenant_id``, ``organization_id``, ``created_at``, ``updated_at``,
``created_by``, ``version``, ``status``, ``metadata``, ``provenance``.

Records are frozen pydantic models. Mutation is expressed as
:meth:`EnterpriseRecord.with_update`, which returns a new instance with a
bumped ``version`` and ``updated_at``; the store enforces optimistic
concurrency on that version.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from matraix.enterprise.errors import EnterpriseSchemaError
from matraix.enterprise.ids import EntityKind, TenantId, new_id

__all__ = ["EnterpriseRecord", "utcnow", "coerce_tenant_id"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def coerce_tenant_id(value: Any) -> str:
    """Normalize a tenant id (``TenantId`` or string) to its canonical value."""
    if isinstance(value, TenantId):
        return value.value
    return TenantId(str(value)).value


class EnterpriseRecord(BaseModel):
    """Base class for every tenant-owned enterprise record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ClassVar[EntityKind]

    id: str
    tenant_id: str
    organization_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    created_by: str | None = None
    version: int = 1
    status: str = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tenant_id")
    @classmethod
    def _tenant(cls, value: str) -> str:
        return coerce_tenant_id(value)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text or len(text) > 160:
            raise EnterpriseSchemaError("record id must be 1-160 characters")
        return text

    @field_validator("version")
    @classmethod
    def _version(cls, value: int) -> int:
        if int(value) < 1:
            raise EnterpriseSchemaError("record version must be >= 1")
        return int(value)

    @classmethod
    def create(cls, **fields: Any) -> Self:
        """Build a new record with a fresh prefixed id and timestamps."""
        fields.setdefault("id", new_id(cls.kind))
        now = utcnow()
        fields.setdefault("created_at", now)
        fields.setdefault("updated_at", now)
        return cls(**fields)

    def with_update(self, **changes: Any) -> Self:
        """Return a modified copy with ``version + 1`` and a new ``updated_at``."""
        for forbidden in ("id", "tenant_id", "created_at", "created_by", "version"):
            if forbidden in changes:
                raise EnterpriseSchemaError(f"{forbidden} is immutable")
        changes["version"] = self.version + 1
        changes["updated_at"] = utcnow()
        return self.model_copy(update=changes)

    def parent_id(self) -> str | None:
        """Optional owning record id used for indexed list-by-parent queries."""
        return None

    def to_document(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_document(cls, payload: dict[str, Any]) -> Self:
        return cls.model_validate(payload)
