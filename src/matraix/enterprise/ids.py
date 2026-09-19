"""Immutable, tenant-scoped identity types.

Every enterprise-owned record carries a :class:`TenantId`. Non-tenant IDs embed
the tenant, so a persona id from tenant A cannot be used as a lookup key for
tenant B without failing the tenant check.

IDs are value objects: frozen, hashable, and constructed only through
validated factories. They are cloud-neutral and have no storage or provider
semantics.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from matraix.enterprise.errors import EnterpriseSchemaError

_ID_BODY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")


class EntityKind(str, Enum):
    TENANT = "tenant"
    ORGANIZATION = "organization"
    DEPARTMENT = "department"
    TEAM = "team"
    USER = "user"
    PERSONA = "persona"
    POPULATION = "population"
    COHORT = "cohort"
    EXPERIMENT = "experiment"
    TASK = "task"
    SCENARIO = "scenario"
    EXECUTION = "execution"
    OBSERVATION = "observation"
    MODEL = "model"
    POLICY = "policy"
    ARTIFACT = "artifact"


_KIND_PREFIX: dict[EntityKind, str] = {
    EntityKind.TENANT: "tnt",
    EntityKind.ORGANIZATION: "org",
    EntityKind.DEPARTMENT: "dep",
    EntityKind.TEAM: "tea",
    EntityKind.USER: "usr",
    EntityKind.PERSONA: "per",
    EntityKind.POPULATION: "pop",
    EntityKind.COHORT: "coh",
    EntityKind.EXPERIMENT: "exp",
    EntityKind.TASK: "tsk",
    EntityKind.SCENARIO: "scn",
    EntityKind.EXECUTION: "exe",
    EntityKind.OBSERVATION: "obs",
    EntityKind.MODEL: "mdl",
    EntityKind.POLICY: "pol",
    EntityKind.ARTIFACT: "art",
}


def _normalize_body(value: str) -> str:
    text = str(value or "").strip().lower()
    if not _ID_BODY_RE.fullmatch(text):
        raise EnterpriseSchemaError(
            "ID body must be 1-128 chars of [a-z0-9_-] starting with "
            f"alphanumeric, got {value!r}"
        )
    return text


def new_id(kind: EntityKind | str) -> str:
    """Return a fresh prefixed identifier for ``kind``."""
    resolved = EntityKind(kind) if not isinstance(kind, EntityKind) else kind
    return f"{_KIND_PREFIX[resolved]}_{uuid.uuid4().hex}"


@dataclass(frozen=True, slots=True)
class TenantId:
    """Root isolation boundary. Never scoped to another tenant."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _normalize_body(self.value))

    def __str__(self) -> str:
        return self.value


class EntityId(Protocol):
    """Tenant-owned identifier protocol used by repositories."""

    tenant_id: TenantId
    value: str
    kind: EntityKind

    def belongs_to(self, tenant_id: TenantId) -> bool: ...


def _scoped_id(kind: EntityKind, class_name: str):
    @dataclass(frozen=True, slots=True)
    class Scoped:
        tenant_id: TenantId
        value: str

        def __post_init__(self) -> None:
            if not isinstance(self.tenant_id, TenantId):
                raise EnterpriseSchemaError(
                    f"{class_name}.tenant_id must be a TenantId"
                )
            object.__setattr__(self, "value", _normalize_body(self.value))

        @property
        def kind(self) -> EntityKind:
            return kind

        def belongs_to(self, tenant_id: TenantId) -> bool:
            return self.tenant_id == tenant_id

        def __str__(self) -> str:
            return f"{kind.value}:{self.tenant_id.value}:{self.value}"

    Scoped.__name__ = class_name
    Scoped.__qualname__ = class_name
    Scoped.__doc__ = f"Immutable {kind.value} id bound to a tenant."
    return Scoped


OrganizationId = _scoped_id(EntityKind.ORGANIZATION, "OrganizationId")
DepartmentId = _scoped_id(EntityKind.DEPARTMENT, "DepartmentId")
TeamId = _scoped_id(EntityKind.TEAM, "TeamId")
UserId = _scoped_id(EntityKind.USER, "UserId")
PersonaId = _scoped_id(EntityKind.PERSONA, "PersonaId")
PopulationId = _scoped_id(EntityKind.POPULATION, "PopulationId")
CohortId = _scoped_id(EntityKind.COHORT, "CohortId")
ExperimentId = _scoped_id(EntityKind.EXPERIMENT, "ExperimentId")
TaskId = _scoped_id(EntityKind.TASK, "TaskId")
ScenarioId = _scoped_id(EntityKind.SCENARIO, "ScenarioId")
ExecutionId = _scoped_id(EntityKind.EXECUTION, "ExecutionId")
ObservationId = _scoped_id(EntityKind.OBSERVATION, "ObservationId")
ModelId = _scoped_id(EntityKind.MODEL, "ModelId")
PolicyId = _scoped_id(EntityKind.POLICY, "PolicyId")
ArtifactId = _scoped_id(EntityKind.ARTIFACT, "ArtifactId")


__all__ = [
    "ArtifactId",
    "CohortId",
    "DepartmentId",
    "EntityId",
    "EntityKind",
    "ExecutionId",
    "ExperimentId",
    "ModelId",
    "ObservationId",
    "OrganizationId",
    "PersonaId",
    "PolicyId",
    "PopulationId",
    "ScenarioId",
    "TaskId",
    "TeamId",
    "TenantId",
    "UserId",
    "new_id",
]
