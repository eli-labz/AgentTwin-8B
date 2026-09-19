"""Core enterprise entities.

These models sit beside the existing MatrAIx persona YAML and Harbor job
records. They do not replace ``persona_id`` / Harbor ``job_name`` identifiers.
Enterprise dimensions are optional simulation parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from matraix.enterprise.errors import EnterpriseSchemaError
from matraix.enterprise.ids import (
    DepartmentId,
    ExperimentId,
    OrganizationId,
    PersonaId,
    PopulationId,
    TeamId,
    TenantId,
    new_id,
    EntityKind,
)
from matraix.enterprise.policy import DataClassification, PolicyDecision

__all__ = [
    "DataClassification",
    "Department",
    "EnterpriseDimensions",
    "EnterprisePersona",
    "ExecutionBudget",
    "Experiment",
    "Organization",
    "Population",
    "Team",
    "Tenant",
]
from matraix.enterprise.persona_schema import (
    EnterpriseDimensions,
    parse_legacy_persona,
    validate_enterprise_dimensions,
)

_SLUG_MAX = 128


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_name(name: str, *, field_name: str = "name") -> str:
    text = str(name or "").strip()
    if not text or len(text) > 256:
        raise EnterpriseSchemaError(
            f"{field_name} must be a non-empty string up to 256 characters"
        )
    return text


def _optional_text(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > 4000:
        raise EnterpriseSchemaError(f"{field_name} exceeds 4000 characters")
    return text


@dataclass(frozen=True, slots=True)
class Tenant:
    id: TenantId
    name: str
    slug: str
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_name(self.name))
        slug = str(self.slug or "").strip().lower()
        if not slug or len(slug) > _SLUG_MAX:
            raise EnterpriseSchemaError("tenant slug must be 1-128 characters")
        object.__setattr__(self, "slug", slug)


@dataclass(frozen=True, slots=True)
class Organization:
    """Business unit / legal entity under a tenant."""

    id: OrganizationId
    tenant_id: TenantId
    name: str
    parent_id: OrganizationId | None = None
    industry: str | None = None
    geography: str | None = None

    def __post_init__(self) -> None:
        if self.id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("organization id tenant mismatch")
        if self.parent_id is not None and self.parent_id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("parent organization is not in this tenant")
        if self.parent_id is not None and self.parent_id == self.id:
            raise EnterpriseSchemaError("organization cannot be its own parent")
        object.__setattr__(self, "name", _require_name(self.name))
        object.__setattr__(
            self, "industry", _optional_text(self.industry, field_name="industry")
        )
        object.__setattr__(
            self, "geography", _optional_text(self.geography, field_name="geography")
        )


@dataclass(frozen=True, slots=True)
class Department:
    id: DepartmentId
    tenant_id: TenantId
    organization_id: OrganizationId
    name: str

    def __post_init__(self) -> None:
        if self.id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("department id tenant mismatch")
        if self.organization_id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("department organization is not in this tenant")
        object.__setattr__(self, "name", _require_name(self.name))


@dataclass(frozen=True, slots=True)
class Team:
    id: TeamId
    tenant_id: TenantId
    organization_id: OrganizationId
    department_id: DepartmentId | None
    name: str

    def __post_init__(self) -> None:
        if self.id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("team id tenant mismatch")
        if self.organization_id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("team organization is not in this tenant")
        if self.department_id is not None and self.department_id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("team department is not in this tenant")
        object.__setattr__(self, "name", _require_name(self.name))


@dataclass(frozen=True, slots=True)
class Population:
    id: PopulationId
    tenant_id: TenantId
    organization_id: OrganizationId
    name: str
    description: str | None = None
    target_size: int | None = None
    team_id: TeamId | None = None

    def __post_init__(self) -> None:
        if self.id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("population id tenant mismatch")
        if self.organization_id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("population organization is not in this tenant")
        if self.team_id is not None and self.team_id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("population team is not in this tenant")
        object.__setattr__(self, "name", _require_name(self.name))
        object.__setattr__(
            self,
            "description",
            _optional_text(self.description, field_name="description"),
        )
        if self.target_size is not None and int(self.target_size) < 1:
            raise EnterpriseSchemaError("population target_size must be >= 1")


@dataclass(frozen=True, slots=True)
class EnterprisePersona:
    """Tenant-owned persona wrapping the existing YAML record.

    ``legacy_persona_id`` and ``dimensions`` preserve today's Playground /
    Harbor ``persona_path`` contract. ``enterprise`` is optional.
    """

    id: PersonaId
    tenant_id: TenantId
    organization_id: OrganizationId
    legacy_persona_id: str
    version: str
    source: str
    dimensions: dict[str, str]
    display_name: str | None = None
    provenance: dict[str, Any] | None = None
    population_id: PopulationId | None = None
    enterprise: EnterpriseDimensions | None = None
    data_classification: DataClassification = DataClassification.INTERNAL

    def __post_init__(self) -> None:
        if self.id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("persona id tenant mismatch")
        if self.organization_id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("persona organization is not in this tenant")
        if self.population_id is not None and self.population_id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("persona population is not in this tenant")
        if not str(self.legacy_persona_id or "").strip():
            raise EnterpriseSchemaError("legacy_persona_id is required")
        object.__setattr__(self, "legacy_persona_id", str(self.legacy_persona_id).strip())
        object.__setattr__(self, "version", str(self.version or "1.0").strip() or "1.0")
        object.__setattr__(self, "source", str(self.source or "").strip())
        if not isinstance(self.dimensions, dict):
            raise EnterpriseSchemaError("dimensions must be a mapping")
        frozen_dims = {str(k): str(v) for k, v in self.dimensions.items()}
        object.__setattr__(self, "dimensions", frozen_dims)
        if self.enterprise is not None:
            validate_enterprise_dimensions(self.enterprise)

    @classmethod
    def from_legacy_record(
        cls,
        *,
        tenant_id: TenantId,
        organization_id: OrganizationId,
        record: Mapping[str, Any],
        population_id: PopulationId | None = None,
        persona_id: PersonaId | None = None,
        catalog: Mapping[str, list[str]] | None = None,
        data_classification: DataClassification = DataClassification.INTERNAL,
    ) -> "EnterprisePersona":
        parsed = parse_legacy_persona(record, catalog=catalog)
        return cls(
            id=persona_id or PersonaId(tenant_id, new_id(EntityKind.PERSONA)),
            tenant_id=tenant_id,
            organization_id=organization_id,
            legacy_persona_id=parsed["persona_id"],
            version=parsed["version"],
            source=parsed["source"],
            dimensions=parsed["dimensions"],
            display_name=parsed.get("display_name"),
            provenance=parsed.get("provenance"),
            population_id=population_id,
            enterprise=parsed.get("enterprise"),
            data_classification=data_classification,
        )


@dataclass(frozen=True, slots=True)
class ExecutionBudget:
    max_tokens: int | None = None
    max_cost: float | None = None
    max_duration_seconds: int | None = None
    max_concurrency: int | None = None

    def __post_init__(self) -> None:
        for name in ("max_tokens", "max_duration_seconds", "max_concurrency"):
            value = getattr(self, name)
            if value is not None and int(value) < 0:
                raise EnterpriseSchemaError(f"{name} must be >= 0")
        if self.max_cost is not None and float(self.max_cost) < 0:
            raise EnterpriseSchemaError("max_cost must be >= 0")


@dataclass(frozen=True, slots=True)
class Experiment:
    """Control-plane skeleton. Does not launch Harbor jobs in this phase."""

    id: ExperimentId
    tenant_id: TenantId
    organization_id: OrganizationId
    hypothesis: str
    objective: str
    population_ids: tuple[PopulationId, ...] = ()
    random_seed: int | None = None
    data_classification: DataClassification = DataClassification.INTERNAL
    default_policy: PolicyDecision = PolicyDecision.SANDBOX_ONLY
    execution_budget: ExecutionBudget = field(default_factory=ExecutionBudget)
    variables: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("experiment id tenant mismatch")
        if self.organization_id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("experiment organization is not in this tenant")
        for population_id in self.population_ids:
            if population_id.tenant_id != self.tenant_id:
                raise EnterpriseSchemaError(
                    "experiment population is not in this tenant"
                )
        object.__setattr__(self, "hypothesis", _require_name(self.hypothesis, field_name="hypothesis"))
        object.__setattr__(self, "objective", _require_name(self.objective, field_name="objective"))
        if self.random_seed is not None:
            object.__setattr__(self, "random_seed", int(self.random_seed))
        if not isinstance(self.default_policy, PolicyDecision):
            raise EnterpriseSchemaError("default_policy must be a PolicyDecision")
        if not isinstance(self.data_classification, DataClassification):
            raise EnterpriseSchemaError(
                "data_classification must be a DataClassification"
            )
