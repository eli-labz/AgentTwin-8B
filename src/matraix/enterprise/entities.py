"""Core enterprise entities.

These models sit beside the existing MatrAIx persona YAML and Harbor job
records. They do not replace ``persona_id`` / Harbor ``job_name`` identifiers.
Enterprise dimensions are optional simulation parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
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
    "EnterpriseExperiment",
    "Experiment",
    "ExperimentGovernance",
    "ExperimentKind",
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "max_cost": self.max_cost,
            "max_duration_seconds": self.max_duration_seconds,
            "max_concurrency": self.max_concurrency,
        }


class ExperimentKind(str, Enum):
    """Metadata only — the Harbor runtime is the same for every kind."""

    BASELINE = "baseline"
    AB = "ab"
    COHORT = "cohort"
    MODEL = "model"
    PROMPT = "prompt"
    POLICY = "policy"
    LATENCY = "latency"
    ACCESSIBILITY = "accessibility"


@dataclass(frozen=True, slots=True)
class ExperimentGovernance:
    """Sign-off and retention metadata. Enforcement of deletion is later."""

    retention_days: int | None = None
    requires_human_validation: bool = True
    sign_off: str | None = None
    notes: str | None = None
    limitations_required: bool = True

    def __post_init__(self) -> None:
        if self.retention_days is not None and int(self.retention_days) < 1:
            raise EnterpriseSchemaError("retention_days must be >= 1")
        if self.retention_days is not None:
            object.__setattr__(self, "retention_days", int(self.retention_days))
        sign_off = _optional_text(self.sign_off, field_name="sign_off")
        notes = _optional_text(self.notes, field_name="notes")
        object.__setattr__(self, "sign_off", sign_off)
        object.__setattr__(self, "notes", notes)
        object.__setattr__(
            self, "requires_human_validation", bool(self.requires_human_validation)
        )
        object.__setattr__(
            self, "limitations_required", bool(self.limitations_required)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "retention_days": self.retention_days,
            "requires_human_validation": self.requires_human_validation,
            "sign_off": self.sign_off,
            "notes": self.notes,
            "limitations_required": self.limitations_required,
        }


def _parse_kind(value: ExperimentKind | str) -> ExperimentKind:
    if isinstance(value, ExperimentKind):
        return value
    try:
        return ExperimentKind(str(value).strip().lower())
    except ValueError as exc:
        raise EnterpriseSchemaError(
            "experiment kind must be one of "
            + ", ".join(item.value for item in ExperimentKind)
        ) from exc


def _parse_policy(value: PolicyDecision | str) -> PolicyDecision:
    if isinstance(value, PolicyDecision):
        return value
    try:
        return PolicyDecision(str(value).strip().upper())
    except ValueError as exc:
        raise EnterpriseSchemaError("default_policy must be a PolicyDecision") from exc


def _parse_classification(
    value: DataClassification | str,
) -> DataClassification:
    if isinstance(value, DataClassification):
        return value
    try:
        return DataClassification(str(value).strip().upper())
    except ValueError as exc:
        raise EnterpriseSchemaError(
            "data_classification must be a DataClassification"
        ) from exc


@dataclass(frozen=True, slots=True)
class Experiment:
    """Enterprise experiment launch record.

    Maps onto existing Harbor job YAML. Does not replace ``harbor.Job`` and
    does not start trials. Default policy is ``SANDBOX_ONLY``.
    """

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
    kind: ExperimentKind = ExperimentKind.BASELINE
    task_path: str | None = None
    model_name: str | None = None
    agent_name: str | None = None
    metrics: tuple[str, ...] = ()
    governance: ExperimentGovernance = field(default_factory=ExperimentGovernance)
    sample_size: int | None = None
    n_attempts: int = 1
    trial_profile: str = "json_survey"
    execution_mode: str = "auto"

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
        object.__setattr__(self, "default_policy", _parse_policy(self.default_policy))
        object.__setattr__(
            self, "data_classification", _parse_classification(self.data_classification)
        )
        object.__setattr__(self, "kind", _parse_kind(self.kind))
        task = _optional_text(self.task_path, field_name="task_path")
        object.__setattr__(self, "task_path", task)
        model = _optional_text(self.model_name, field_name="model_name")
        object.__setattr__(self, "model_name", model)
        agent = _optional_text(self.agent_name, field_name="agent_name")
        object.__setattr__(self, "agent_name", agent)
        metrics = tuple(
            str(item).strip() for item in self.metrics if str(item).strip()
        )
        object.__setattr__(self, "metrics", metrics)
        if not isinstance(self.governance, ExperimentGovernance):
            raise EnterpriseSchemaError("governance must be ExperimentGovernance")
        if not isinstance(self.execution_budget, ExecutionBudget):
            raise EnterpriseSchemaError("execution_budget must be ExecutionBudget")
        if not isinstance(self.variables, dict):
            raise EnterpriseSchemaError("variables must be an object")
        object.__setattr__(
            self, "variables", {str(k): str(v) for k, v in self.variables.items()}
        )
        if self.sample_size is not None and int(self.sample_size) < 1:
            raise EnterpriseSchemaError("sample_size must be >= 1")
        if self.sample_size is not None:
            object.__setattr__(self, "sample_size", int(self.sample_size))
        if int(self.n_attempts) < 1:
            raise EnterpriseSchemaError("n_attempts must be >= 1")
        object.__setattr__(self, "n_attempts", int(self.n_attempts))
        profile = str(self.trial_profile or "json_survey").strip() or "json_survey"
        mode = str(self.execution_mode or "auto").strip().lower() or "auto"
        object.__setattr__(self, "trial_profile", profile)
        object.__setattr__(self, "execution_mode", mode)

    def launch_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "task_path": self.task_path,
            "model_name": self.model_name,
            "agent_name": self.agent_name,
            "metrics": list(self.metrics),
            "governance": self.governance.to_dict(),
            "sample_size": self.sample_size,
            "n_attempts": self.n_attempts,
            "trial_profile": self.trial_profile,
            "execution_mode": self.execution_mode,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id.value,
            "tenant_id": self.tenant_id.value,
            "organization_id": self.organization_id.value,
            "hypothesis": self.hypothesis,
            "objective": self.objective,
            "population_ids": [item.value for item in self.population_ids],
            "random_seed": self.random_seed,
            "data_classification": self.data_classification.value,
            "default_policy": self.default_policy.value,
            "execution_budget": self.execution_budget.to_dict(),
            "variables": dict(self.variables),
            **self.launch_payload(),
        }


EnterpriseExperiment = Experiment
