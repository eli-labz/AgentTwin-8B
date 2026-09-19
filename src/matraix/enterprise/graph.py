"""Organizational graph edges.

These relationships sit beside Department / Team / Persona records. They do
not replace Harbor jobs or the persona DAG. Cross-tenant endpoints are
rejected at construction; stores still isolate lookups by TenantId.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from matraix.enterprise.errors import EnterpriseSchemaError
from matraix.enterprise.ids import (
    DepartmentId,
    OrganizationId,
    OrgEdgeId,
    PersonaId,
    TeamId,
    TenantId,
    _normalize_body,
)

__all__ = [
    "ORG_RELATIONS",
    "OrgEdge",
    "OrgNodeKind",
    "OrgRelation",
    "require_org_node",
]


class OrgRelation(str, Enum):
    REPORTS_TO = "reports_to"
    MEMBER_OF = "member_of"
    COLLABORATES_WITH = "collaborates_with"
    DEPENDS_ON = "depends_on"
    APPROVES = "approves"
    ESCALATES_TO = "escalates_to"
    SERVES = "serves"
    SUPPLIES = "supplies"
    REVIEWS = "reviews"
    OWNS_PROCESS = "owns_process"
    OWNS_SYSTEM = "owns_system"


ORG_RELATIONS: tuple[OrgRelation, ...] = tuple(OrgRelation)


class OrgNodeKind(str, Enum):
    PERSONA = "persona"
    TEAM = "team"
    DEPARTMENT = "department"
    ORGANIZATION = "organization"
    PROCESS = "process"
    SYSTEM = "system"


_ENTITY_KINDS = frozenset(
    {
        OrgNodeKind.PERSONA,
        OrgNodeKind.TEAM,
        OrgNodeKind.DEPARTMENT,
        OrgNodeKind.ORGANIZATION,
    }
)

_RELATION_ENDS: dict[OrgRelation, tuple[frozenset[OrgNodeKind], frozenset[OrgNodeKind]]] = {
    OrgRelation.REPORTS_TO: (
        frozenset({OrgNodeKind.PERSONA}),
        frozenset({OrgNodeKind.PERSONA}),
    ),
    OrgRelation.MEMBER_OF: (
        frozenset({OrgNodeKind.PERSONA}),
        frozenset(
            {OrgNodeKind.TEAM, OrgNodeKind.DEPARTMENT, OrgNodeKind.ORGANIZATION}
        ),
    ),
    OrgRelation.OWNS_PROCESS: (
        frozenset(_ENTITY_KINDS),
        frozenset({OrgNodeKind.PROCESS}),
    ),
    OrgRelation.OWNS_SYSTEM: (
        frozenset(_ENTITY_KINDS),
        frozenset({OrgNodeKind.SYSTEM}),
    ),
}


def _parse_relation(value: OrgRelation | str) -> OrgRelation:
    if isinstance(value, OrgRelation):
        return value
    try:
        return OrgRelation(str(value).strip().lower())
    except ValueError as exc:
        raise EnterpriseSchemaError(
            f"unknown org relation {value!r}; expected one of "
            + ", ".join(item.value for item in OrgRelation)
        ) from exc


def _parse_kind(value: OrgNodeKind | str, *, field_name: str) -> OrgNodeKind:
    if isinstance(value, OrgNodeKind):
        return value
    try:
        return OrgNodeKind(str(value).strip().lower())
    except ValueError as exc:
        raise EnterpriseSchemaError(
            f"{field_name} must be one of "
            + ", ".join(item.value for item in OrgNodeKind)
        ) from exc


def _attributes(value: Mapping[str, Any] | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise EnterpriseSchemaError("edge attributes must be an object")
    out: dict[str, str] = {}
    for key, item in value.items():
        name = str(key or "").strip()
        if not name or len(name) > 128:
            raise EnterpriseSchemaError("attribute name must be 1-128 characters")
        text = str(item).strip()
        if not text or len(text) > 512:
            raise EnterpriseSchemaError(f"attribute {name!r} must be 1-512 characters")
        out[name] = text
    return out


@dataclass(frozen=True, slots=True)
class OrgEdge:
    """Directed, tenant-owned organizational relationship."""

    id: OrgEdgeId
    tenant_id: TenantId
    organization_id: OrganizationId
    relation: OrgRelation
    source_kind: OrgNodeKind
    source_id: str
    target_kind: OrgNodeKind
    target_id: str
    attributes: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("org edge id tenant mismatch")
        if self.organization_id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("org edge organization is not in this tenant")
        relation = _parse_relation(self.relation)
        source_kind = _parse_kind(self.source_kind, field_name="source_kind")
        target_kind = _parse_kind(self.target_kind, field_name="target_kind")
        object.__setattr__(self, "relation", relation)
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "target_kind", target_kind)
        object.__setattr__(self, "source_id", _normalize_body(self.source_id))
        object.__setattr__(self, "target_id", _normalize_body(self.target_id))
        object.__setattr__(self, "attributes", _attributes(self.attributes))
        allowed = _RELATION_ENDS.get(relation)
        if allowed is not None:
            sources, targets = allowed
            if source_kind not in sources:
                raise EnterpriseSchemaError(
                    f"{relation.value} source_kind must be one of "
                    + ", ".join(sorted(item.value for item in sources))
                )
            if target_kind not in targets:
                raise EnterpriseSchemaError(
                    f"{relation.value} target_kind must be one of "
                    + ", ".join(sorted(item.value for item in targets))
                )
        if (
            relation is OrgRelation.REPORTS_TO
            and self.source_id == self.target_id
            and source_kind == target_kind
        ):
            raise EnterpriseSchemaError("reports_to cannot be a self-loop")

    def identity_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.relation.value,
            self.source_kind.value,
            self.source_id,
            self.target_kind.value,
            self.target_id,
        )


def require_org_node(store: Any, tenant_id: TenantId, kind: OrgNodeKind, local_id: str) -> None:
    """Resolve an entity-kind node inside ``tenant_id``. Process/system are labels."""
    body = _normalize_body(local_id)
    if kind is OrgNodeKind.PERSONA:
        store.get_persona(tenant_id, PersonaId(tenant_id, body))
    elif kind is OrgNodeKind.TEAM:
        store.get_team(tenant_id, TeamId(tenant_id, body))
    elif kind is OrgNodeKind.DEPARTMENT:
        store.get_department(tenant_id, DepartmentId(tenant_id, body))
    elif kind is OrgNodeKind.ORGANIZATION:
        store.get_organization(tenant_id, OrganizationId(tenant_id, body))
    elif kind in {OrgNodeKind.PROCESS, OrgNodeKind.SYSTEM}:
        return
    else:
        raise EnterpriseSchemaError(f"unsupported org node kind {kind}")
