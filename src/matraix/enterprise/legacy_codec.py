"""JSON codec for the Phase 0 dataclass entities.

Used by stores that persist every entity as a JSON document (PostgreSQL) and
by API/SDK serialization. Reconstructing through the entity constructors keeps
tenant and schema checks on the domain types.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from matraix.enterprise.entities import (
    Department,
    EnterprisePersona,
    ExecutionBudget,
    Experiment,
    Organization,
    Population,
    Team,
    Tenant,
)
from matraix.enterprise.graph import OrgEdge, OrgNodeKind, OrgRelation
from matraix.enterprise.ids import (
    DepartmentId,
    ExperimentId,
    OrganizationId,
    OrgEdgeId,
    PersonaId,
    PopulationId,
    TeamId,
    TenantId,
)
from matraix.enterprise.persona_schema import parse_enterprise_block
from matraix.enterprise.policy import DataClassification, PolicyDecision
from matraix.enterprise.population_builder import (
    GenerationBackend,
    PopulationDeclaration,
    PopulationSegment,
)

__all__ = ["encode_entity", "decode_entity", "LEGACY_KINDS"]

LEGACY_KINDS = {
    Tenant: "legacy_tenant",
    Organization: "legacy_organization",
    Department: "legacy_department",
    Team: "legacy_team",
    Population: "legacy_population",
    EnterprisePersona: "legacy_persona",
    Experiment: "legacy_experiment",
    OrgEdge: "legacy_org_edge",
    PopulationDeclaration: "legacy_population_declaration",
}


def _opt(value: Any) -> str | None:
    return value.value if value is not None else None


def encode_entity(entity: Any) -> dict[str, Any]:
    if isinstance(entity, Tenant):
        return {
            "id": entity.id.value,
            "name": entity.name,
            "slug": entity.slug,
            "created_at": entity.created_at.isoformat(),
        }
    if isinstance(entity, Organization):
        return {
            "id": entity.id.value,
            "tenant_id": entity.tenant_id.value,
            "name": entity.name,
            "parent_id": _opt(entity.parent_id),
            "industry": entity.industry,
            "geography": entity.geography,
        }
    if isinstance(entity, Department):
        return {
            "id": entity.id.value,
            "tenant_id": entity.tenant_id.value,
            "organization_id": entity.organization_id.value,
            "name": entity.name,
        }
    if isinstance(entity, Team):
        return {
            "id": entity.id.value,
            "tenant_id": entity.tenant_id.value,
            "organization_id": entity.organization_id.value,
            "department_id": _opt(entity.department_id),
            "name": entity.name,
        }
    if isinstance(entity, Population):
        return {
            "id": entity.id.value,
            "tenant_id": entity.tenant_id.value,
            "organization_id": entity.organization_id.value,
            "name": entity.name,
            "description": entity.description,
            "target_size": entity.target_size,
            "team_id": _opt(entity.team_id),
        }
    if isinstance(entity, EnterprisePersona):
        return {
            "id": entity.id.value,
            "tenant_id": entity.tenant_id.value,
            "organization_id": entity.organization_id.value,
            "legacy_persona_id": entity.legacy_persona_id,
            "version": entity.version,
            "source": entity.source,
            "dimensions": dict(entity.dimensions),
            "display_name": entity.display_name,
            "provenance": entity.provenance,
            "population_id": _opt(entity.population_id),
            "enterprise": entity.enterprise.to_dict() if entity.enterprise else None,
            "data_classification": entity.data_classification.value,
        }
    if isinstance(entity, Experiment):
        budget = entity.execution_budget
        return {
            "id": entity.id.value,
            "tenant_id": entity.tenant_id.value,
            "organization_id": entity.organization_id.value,
            "hypothesis": entity.hypothesis,
            "objective": entity.objective,
            "population_ids": [item.value for item in entity.population_ids],
            "random_seed": entity.random_seed,
            "data_classification": entity.data_classification.value,
            "default_policy": entity.default_policy.value,
            "execution_budget": {
                "max_tokens": budget.max_tokens,
                "max_cost": budget.max_cost,
                "max_duration_seconds": budget.max_duration_seconds,
                "max_concurrency": budget.max_concurrency,
            },
            "variables": dict(entity.variables),
        }
    if isinstance(entity, OrgEdge):
        return {
            "id": entity.id.value,
            "tenant_id": entity.tenant_id.value,
            "organization_id": entity.organization_id.value,
            "relation": entity.relation.value,
            "source_kind": entity.source_kind.value,
            "source_id": entity.source_id,
            "target_kind": entity.target_kind.value,
            "target_id": entity.target_id,
            "attributes": dict(entity.attributes),
        }
    if isinstance(entity, PopulationDeclaration):
        return entity.to_dict()
    raise TypeError(f"unsupported legacy entity {type(entity).__name__}")


def decode_entity(kind: str, payload: dict[str, Any]) -> Any:
    if kind == "legacy_tenant":
        return Tenant(
            id=TenantId(payload["id"]),
            name=payload["name"],
            slug=payload["slug"],
            created_at=datetime.fromisoformat(payload["created_at"]),
        )
    tenant = TenantId(payload["tenant_id"])
    if kind == "legacy_organization":
        return Organization(
            id=OrganizationId(tenant, payload["id"]),
            tenant_id=tenant,
            name=payload["name"],
            parent_id=OrganizationId(tenant, payload["parent_id"]) if payload.get("parent_id") else None,
            industry=payload.get("industry"),
            geography=payload.get("geography"),
        )
    if kind == "legacy_department":
        return Department(
            id=DepartmentId(tenant, payload["id"]),
            tenant_id=tenant,
            organization_id=OrganizationId(tenant, payload["organization_id"]),
            name=payload["name"],
        )
    if kind == "legacy_team":
        return Team(
            id=TeamId(tenant, payload["id"]),
            tenant_id=tenant,
            organization_id=OrganizationId(tenant, payload["organization_id"]),
            department_id=DepartmentId(tenant, payload["department_id"]) if payload.get("department_id") else None,
            name=payload["name"],
        )
    if kind == "legacy_population":
        return Population(
            id=PopulationId(tenant, payload["id"]),
            tenant_id=tenant,
            organization_id=OrganizationId(tenant, payload["organization_id"]),
            name=payload["name"],
            description=payload.get("description"),
            target_size=payload.get("target_size"),
            team_id=TeamId(tenant, payload["team_id"]) if payload.get("team_id") else None,
        )
    if kind == "legacy_persona":
        return EnterprisePersona(
            id=PersonaId(tenant, payload["id"]),
            tenant_id=tenant,
            organization_id=OrganizationId(tenant, payload["organization_id"]),
            legacy_persona_id=payload["legacy_persona_id"],
            version=payload["version"],
            source=payload["source"],
            dimensions=dict(payload["dimensions"]),
            display_name=payload.get("display_name"),
            provenance=payload.get("provenance"),
            population_id=PopulationId(tenant, payload["population_id"]) if payload.get("population_id") else None,
            enterprise=parse_enterprise_block(payload["enterprise"]) if payload.get("enterprise") else None,
            data_classification=DataClassification(payload.get("data_classification", "INTERNAL")),
        )
    if kind == "legacy_experiment":
        budget = payload.get("execution_budget") or {}
        return Experiment(
            id=ExperimentId(tenant, payload["id"]),
            tenant_id=tenant,
            organization_id=OrganizationId(tenant, payload["organization_id"]),
            hypothesis=payload["hypothesis"],
            objective=payload["objective"],
            population_ids=tuple(PopulationId(tenant, item) for item in payload.get("population_ids", [])),
            random_seed=payload.get("random_seed"),
            data_classification=DataClassification(payload.get("data_classification", "INTERNAL")),
            default_policy=PolicyDecision(payload.get("default_policy", "SANDBOX_ONLY")),
            execution_budget=ExecutionBudget(
                max_tokens=budget.get("max_tokens"),
                max_cost=budget.get("max_cost"),
                max_duration_seconds=budget.get("max_duration_seconds"),
                max_concurrency=budget.get("max_concurrency"),
            ),
            variables=dict(payload.get("variables") or {}),
        )
    if kind == "legacy_org_edge":
        return OrgEdge(
            id=OrgEdgeId(tenant, payload["id"]),
            tenant_id=tenant,
            organization_id=OrganizationId(tenant, payload["organization_id"]),
            relation=OrgRelation(payload["relation"]),
            source_kind=OrgNodeKind(payload["source_kind"]),
            source_id=payload["source_id"],
            target_kind=OrgNodeKind(payload["target_kind"]),
            target_id=payload["target_id"],
            attributes=dict(payload.get("attributes") or {}),
        )
    if kind == "legacy_population_declaration":
        segments = tuple(
            PopulationSegment(
                name=item["name"],
                count=item.get("count"),
                share=item.get("share"),
                filters={key: tuple(vals) for key, vals in (item.get("filters") or {}).items()},
                constraints=dict(item.get("constraints") or {}),
            )
            for item in payload["segments"]
        )
        return PopulationDeclaration(
            tenant_id=tenant,
            organization_id=OrganizationId(tenant, payload["organization_id"]),
            population_id=PopulationId(tenant, payload["population_id"]),
            target_size=int(payload["target_size"]),
            backend=GenerationBackend(payload["backend"]),
            segments=segments,
            resolved_counts=tuple(int(item) for item in payload["resolved_counts"]),
            constraints=tuple(payload.get("constraints") or ()),
            include_org_structure=bool(payload.get("include_org_structure")),
            privacy_mode=payload.get("privacy_mode", "aggregate_stats_then_synthetic"),
        )
    raise TypeError(f"unsupported legacy kind {kind}")
