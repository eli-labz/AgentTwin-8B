"""Tenant-bound repository contracts and an in-memory implementation.

Lookups always take a :class:`TenantId`. A scoped id from another tenant raises
:class:`CrossTenantAccessError` instead of returning that tenant's data. Stores
are partitioned by tenant so a missing local id cannot leak across tenants.

:class:`InMemoryEnterpriseStore` is the default / test backend. Durable
deployments use :class:`matraix.enterprise.sqlite_store.SqliteEnterpriseStore`
via :func:`matraix.enterprise.store.open_enterprise_store`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, TypeVar

if TYPE_CHECKING:
    from matraix.enterprise.runtime import Artifact, ExecutionRecord

from matraix.enterprise.entities import (
    Department,
    EnterprisePersona,
    Experiment,
    Organization,
    Population,
    Team,
    Tenant,
)
from matraix.enterprise.errors import (
    CrossTenantAccessError,
    EnterpriseSchemaError,
    EntityNotFoundError,
)
from matraix.enterprise.graph import OrgEdge, OrgRelation, require_org_node
from matraix.enterprise.audit import AuditEvent
from matraix.enterprise.governance import GovernanceReview
from matraix.enterprise.identity import EnterpriseUser
from matraix.enterprise.ids import (
    ArtifactId,
    DepartmentId,
    EntityId,
    ExecutionId,
    ExperimentId,
    OrganizationId,
    OrgEdgeId,
    PersonaId,
    PopulationId,
    TeamId,
    TenantId,
    UserId,
)
from matraix.enterprise.events import EnterpriseEvent
from matraix.enterprise.model_gateway import ModelPolicy, default_model_policy
from matraix.enterprise.population_builder import PopulationDeclaration

T = TypeVar("T")


class EnterpriseRepository(Protocol):
    """Persistence contract shared by in-memory and SQLite backends.

    Implementations must not put business rules in an ORM layer — they
    reconstruct domain entities and reuse constructor / tenant checks.
    """

    def put_tenant(self, tenant: Tenant) -> Tenant: ...

    def get_tenant(self, tenant_id: TenantId) -> Tenant: ...

    def list_tenants(self) -> list[Tenant]: ...

    def put_organization(self, organization: Organization) -> Organization: ...

    def get_organization(
        self, tenant_id: TenantId, organization_id: OrganizationId
    ) -> Organization: ...

    def list_organizations(self, tenant_id: TenantId) -> list[Organization]: ...

    def put_department(self, department: Department) -> Department: ...

    def get_department(
        self, tenant_id: TenantId, department_id: DepartmentId
    ) -> Department: ...

    def put_team(self, team: Team) -> Team: ...

    def get_team(self, tenant_id: TenantId, team_id: TeamId) -> Team: ...

    def put_population(self, population: Population) -> Population: ...

    def get_population(
        self, tenant_id: TenantId, population_id: PopulationId
    ) -> Population: ...

    def list_populations(self, tenant_id: TenantId) -> list[Population]: ...

    def put_persona(self, persona: EnterprisePersona) -> EnterprisePersona: ...

    def get_persona(
        self, tenant_id: TenantId, persona_id: PersonaId
    ) -> EnterprisePersona: ...

    def list_personas(self, tenant_id: TenantId) -> list[EnterprisePersona]: ...

    def put_experiment(self, experiment: Experiment) -> Experiment: ...

    def get_experiment(
        self, tenant_id: TenantId, experiment_id: ExperimentId
    ) -> Experiment: ...

    def list_experiments(self, tenant_id: TenantId) -> list[Experiment]: ...

    def put_org_edge(self, edge: OrgEdge) -> OrgEdge: ...

    def get_org_edge(self, tenant_id: TenantId, edge_id: OrgEdgeId) -> OrgEdge: ...

    def list_org_edges(
        self,
        tenant_id: TenantId,
        *,
        organization_id: OrganizationId | None = None,
        relation: OrgRelation | None = None,
    ) -> list[OrgEdge]: ...

    def delete_org_edge(self, tenant_id: TenantId, edge_id: OrgEdgeId) -> None: ...

    def put_population_declaration(
        self, declaration: PopulationDeclaration
    ) -> PopulationDeclaration: ...

    def get_population_declaration(
        self, tenant_id: TenantId, population_id: PopulationId
    ) -> PopulationDeclaration: ...

    def put_model_policy(self, policy: ModelPolicy) -> ModelPolicy: ...

    def get_model_policy(self, tenant_id: TenantId) -> ModelPolicy: ...

    def put_execution(self, record: ExecutionRecord) -> ExecutionRecord: ...

    def get_execution(
        self, tenant_id: TenantId, execution_id: ExecutionId
    ) -> ExecutionRecord: ...

    def list_executions(self, tenant_id: TenantId) -> list[ExecutionRecord]: ...

    def put_artifact(self, artifact: Artifact) -> Artifact: ...

    def get_artifact(
        self, tenant_id: TenantId, artifact_id: ArtifactId
    ) -> Artifact: ...

    def list_artifacts(
        self,
        tenant_id: TenantId,
        *,
        execution_id: ExecutionId | None = None,
    ) -> list[Artifact]: ...

    def put_event(self, event: EnterpriseEvent) -> EnterpriseEvent: ...

    def list_events(
        self,
        tenant_id: TenantId,
        *,
        execution_id: ExecutionId | None = None,
    ) -> list[EnterpriseEvent]: ...

    def put_user(self, user: EnterpriseUser) -> EnterpriseUser: ...

    def get_user(self, tenant_id: TenantId, user_id: UserId) -> EnterpriseUser: ...

    def list_users(self, tenant_id: TenantId) -> list[EnterpriseUser]: ...

    def append_audit(self, event: AuditEvent) -> AuditEvent: ...

    def list_audit(self, tenant_id: TenantId) -> list[AuditEvent]: ...

    def export_audit(self, tenant_id: TenantId) -> list[dict]: ...

    def update_audit(self, event_id: str, **changes: object) -> None: ...

    def delete_audit(self, event_id: str) -> None: ...

    def put_governance_review(self, review: GovernanceReview) -> GovernanceReview: ...

    def list_governance_reviews(
        self, tenant_id: TenantId
    ) -> list[GovernanceReview]: ...

    def close(self) -> None: ...


def _require_tenant(tenant_id: TenantId, entity_id: EntityId) -> None:
    if not entity_id.belongs_to(tenant_id):
        raise CrossTenantAccessError(
            f"{entity_id.kind.value} {entity_id.value} is not owned by tenant "
            f"{tenant_id.value}"
        )


@dataclass
class _TenantBucket:
    organizations: dict[str, Organization] = field(default_factory=dict)
    departments: dict[str, Department] = field(default_factory=dict)
    teams: dict[str, Team] = field(default_factory=dict)
    populations: dict[str, Population] = field(default_factory=dict)
    personas: dict[str, EnterprisePersona] = field(default_factory=dict)
    experiments: dict[str, Experiment] = field(default_factory=dict)
    org_edges: dict[str, OrgEdge] = field(default_factory=dict)
    population_declarations: dict[str, PopulationDeclaration] = field(
        default_factory=dict
    )
    model_policy: ModelPolicy | None = None
    executions: dict[str, ExecutionRecord] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)
    events: dict[str, EnterpriseEvent] = field(default_factory=dict)
    users: dict[str, EnterpriseUser] = field(default_factory=dict)
    audit: list[AuditEvent] = field(default_factory=list)
    reviews: dict[str, GovernanceReview] = field(default_factory=dict)


class InMemoryEnterpriseStore:
    """Process-local store sufficient for unit and tenancy tests."""

    def __init__(self) -> None:
        self._tenants: dict[str, Tenant] = {}
        self._buckets: dict[str, _TenantBucket] = {}

    def _bucket(self, tenant_id: TenantId) -> _TenantBucket:
        if tenant_id.value not in self._tenants:
            raise EntityNotFoundError(f"unknown tenant {tenant_id.value}")
        return self._buckets[tenant_id.value]

    def put_tenant(self, tenant: Tenant) -> Tenant:
        for existing in self._tenants.values():
            if existing.slug == tenant.slug and existing.id != tenant.id:
                raise EnterpriseSchemaError(
                    f"tenant slug {tenant.slug!r} already exists"
                )
        self._tenants[tenant.id.value] = tenant
        self._buckets.setdefault(tenant.id.value, _TenantBucket())
        return tenant

    def get_tenant(self, tenant_id: TenantId) -> Tenant:
        try:
            return self._tenants[tenant_id.value]
        except KeyError as exc:
            raise EntityNotFoundError(f"unknown tenant {tenant_id.value}") from exc

    def list_tenants(self) -> list[Tenant]:
        return list(self._tenants.values())

    def put_organization(self, organization: Organization) -> Organization:
        self._bucket(organization.tenant_id).organizations[organization.id.value] = (
            organization
        )
        return organization

    def get_organization(
        self, tenant_id: TenantId, organization_id: OrganizationId
    ) -> Organization:
        _require_tenant(tenant_id, organization_id)
        try:
            return self._bucket(tenant_id).organizations[organization_id.value]
        except KeyError as exc:
            raise EntityNotFoundError(
                f"unknown organization {organization_id.value}"
            ) from exc

    def list_organizations(self, tenant_id: TenantId) -> list[Organization]:
        return list(self._bucket(tenant_id).organizations.values())

    def put_department(self, department: Department) -> Department:
        bucket = self._bucket(department.tenant_id)
        if department.organization_id.value not in bucket.organizations:
            raise EntityNotFoundError(
                f"unknown organization {department.organization_id.value}"
            )
        bucket.departments[department.id.value] = department
        return department

    def get_department(
        self, tenant_id: TenantId, department_id: DepartmentId
    ) -> Department:
        _require_tenant(tenant_id, department_id)
        try:
            return self._bucket(tenant_id).departments[department_id.value]
        except KeyError as exc:
            raise EntityNotFoundError(
                f"unknown department {department_id.value}"
            ) from exc

    def put_team(self, team: Team) -> Team:
        bucket = self._bucket(team.tenant_id)
        if team.organization_id.value not in bucket.organizations:
            raise EntityNotFoundError(
                f"unknown organization {team.organization_id.value}"
            )
        if team.department_id is not None and team.department_id.value not in bucket.departments:
            raise EntityNotFoundError(f"unknown department {team.department_id.value}")
        bucket.teams[team.id.value] = team
        return team

    def get_team(self, tenant_id: TenantId, team_id: TeamId) -> Team:
        _require_tenant(tenant_id, team_id)
        try:
            return self._bucket(tenant_id).teams[team_id.value]
        except KeyError as exc:
            raise EntityNotFoundError(f"unknown team {team_id.value}") from exc

    def put_population(self, population: Population) -> Population:
        bucket = self._bucket(population.tenant_id)
        if population.organization_id.value not in bucket.organizations:
            raise EntityNotFoundError(
                f"unknown organization {population.organization_id.value}"
            )
        bucket.populations[population.id.value] = population
        return population

    def get_population(
        self, tenant_id: TenantId, population_id: PopulationId
    ) -> Population:
        _require_tenant(tenant_id, population_id)
        try:
            return self._bucket(tenant_id).populations[population_id.value]
        except KeyError as exc:
            raise EntityNotFoundError(
                f"unknown population {population_id.value}"
            ) from exc

    def list_populations(self, tenant_id: TenantId) -> list[Population]:
        return list(self._bucket(tenant_id).populations.values())

    def put_persona(self, persona: EnterprisePersona) -> EnterprisePersona:
        bucket = self._bucket(persona.tenant_id)
        if persona.organization_id.value not in bucket.organizations:
            raise EntityNotFoundError(
                f"unknown organization {persona.organization_id.value}"
            )
        if (
            persona.population_id is not None
            and persona.population_id.value not in bucket.populations
        ):
            raise EntityNotFoundError(
                f"unknown population {persona.population_id.value}"
            )
        bucket.personas[persona.id.value] = persona
        return persona

    def get_persona(self, tenant_id: TenantId, persona_id: PersonaId) -> EnterprisePersona:
        _require_tenant(tenant_id, persona_id)
        try:
            return self._bucket(tenant_id).personas[persona_id.value]
        except KeyError as exc:
            raise EntityNotFoundError(f"unknown persona {persona_id.value}") from exc

    def list_personas(self, tenant_id: TenantId) -> list[EnterprisePersona]:
        return list(self._bucket(tenant_id).personas.values())

    def put_experiment(self, experiment: Experiment) -> Experiment:
        bucket = self._bucket(experiment.tenant_id)
        if experiment.organization_id.value not in bucket.organizations:
            raise EntityNotFoundError(
                f"unknown organization {experiment.organization_id.value}"
            )
        for population_id in experiment.population_ids:
            if population_id.value not in bucket.populations:
                raise EntityNotFoundError(f"unknown population {population_id.value}")
        bucket.experiments[experiment.id.value] = experiment
        return experiment

    def get_experiment(
        self, tenant_id: TenantId, experiment_id: ExperimentId
    ) -> Experiment:
        _require_tenant(tenant_id, experiment_id)
        try:
            return self._bucket(tenant_id).experiments[experiment_id.value]
        except KeyError as exc:
            raise EntityNotFoundError(
                f"unknown experiment {experiment_id.value}"
            ) from exc

    def list_experiments(self, tenant_id: TenantId) -> list[Experiment]:
        return list(self._bucket(tenant_id).experiments.values())

    def put_org_edge(self, edge: OrgEdge) -> OrgEdge:
        bucket = self._bucket(edge.tenant_id)
        if edge.organization_id.value not in bucket.organizations:
            raise EntityNotFoundError(
                f"unknown organization {edge.organization_id.value}"
            )
        require_org_node(self, edge.tenant_id, edge.source_kind, edge.source_id)
        require_org_node(self, edge.tenant_id, edge.target_kind, edge.target_id)
        for existing in bucket.org_edges.values():
            if existing.id != edge.id and existing.identity_key() == edge.identity_key():
                raise EnterpriseSchemaError("duplicate org edge")
        bucket.org_edges[edge.id.value] = edge
        return edge

    def get_org_edge(self, tenant_id: TenantId, edge_id: OrgEdgeId) -> OrgEdge:
        _require_tenant(tenant_id, edge_id)
        try:
            return self._bucket(tenant_id).org_edges[edge_id.value]
        except KeyError as exc:
            raise EntityNotFoundError(f"unknown org edge {edge_id.value}") from exc

    def list_org_edges(
        self,
        tenant_id: TenantId,
        *,
        organization_id: OrganizationId | None = None,
        relation: OrgRelation | None = None,
    ) -> list[OrgEdge]:
        items = list(self._bucket(tenant_id).org_edges.values())
        if organization_id is not None:
            _require_tenant(tenant_id, organization_id)
            items = [item for item in items if item.organization_id == organization_id]
        if relation is not None:
            items = [item for item in items if item.relation is relation]
        return items

    def delete_org_edge(self, tenant_id: TenantId, edge_id: OrgEdgeId) -> None:
        self.get_org_edge(tenant_id, edge_id)
        del self._bucket(tenant_id).org_edges[edge_id.value]

    def put_population_declaration(
        self, declaration: PopulationDeclaration
    ) -> PopulationDeclaration:
        bucket = self._bucket(declaration.tenant_id)
        if declaration.population_id.value not in bucket.populations:
            raise EntityNotFoundError(
                f"unknown population {declaration.population_id.value}"
            )
        if declaration.organization_id.value not in bucket.organizations:
            raise EntityNotFoundError(
                f"unknown organization {declaration.organization_id.value}"
            )
        bucket.population_declarations[declaration.population_id.value] = declaration
        return declaration

    def get_population_declaration(
        self, tenant_id: TenantId, population_id: PopulationId
    ) -> PopulationDeclaration:
        _require_tenant(tenant_id, population_id)
        try:
            return self._bucket(tenant_id).population_declarations[population_id.value]
        except KeyError as exc:
            raise EntityNotFoundError(
                f"unknown population declaration {population_id.value}"
            ) from exc

    def put_model_policy(self, policy: ModelPolicy) -> ModelPolicy:
        bucket = self._bucket(policy.tenant_id)
        bucket.model_policy = policy
        return policy

    def get_model_policy(self, tenant_id: TenantId) -> ModelPolicy:
        stored = self._bucket(tenant_id).model_policy
        if stored is None:
            return default_model_policy(tenant_id)
        return stored

    def put_execution(self, record: ExecutionRecord) -> ExecutionRecord:
        self._bucket(record.tenant_id).executions[record.id.value] = record
        return record

    def get_execution(
        self, tenant_id: TenantId, execution_id: ExecutionId
    ) -> ExecutionRecord:
        _require_tenant(tenant_id, execution_id)
        try:
            return self._bucket(tenant_id).executions[execution_id.value]
        except KeyError as exc:
            raise EntityNotFoundError(
                f"unknown execution {execution_id.value}"
            ) from exc

    def list_executions(self, tenant_id: TenantId) -> list[ExecutionRecord]:
        return list(self._bucket(tenant_id).executions.values())

    def put_artifact(self, artifact: Artifact) -> Artifact:
        self._bucket(artifact.tenant_id).artifacts[artifact.id.value] = artifact
        return artifact

    def get_artifact(
        self, tenant_id: TenantId, artifact_id: ArtifactId
    ) -> Artifact:
        _require_tenant(tenant_id, artifact_id)
        try:
            return self._bucket(tenant_id).artifacts[artifact_id.value]
        except KeyError as exc:
            raise EntityNotFoundError(f"unknown artifact {artifact_id.value}") from exc

    def list_artifacts(
        self,
        tenant_id: TenantId,
        *,
        execution_id: ExecutionId | None = None,
    ) -> list[Artifact]:
        items = list(self._bucket(tenant_id).artifacts.values())
        if execution_id is not None:
            _require_tenant(tenant_id, execution_id)
            items = [item for item in items if item.execution_id == execution_id]
        return items

    def put_event(self, event: EnterpriseEvent) -> EnterpriseEvent:
        self._bucket(event.tenant_id).events[event.id.value] = event
        return event

    def list_events(
        self,
        tenant_id: TenantId,
        *,
        execution_id: ExecutionId | None = None,
    ) -> list[EnterpriseEvent]:
        items = list(self._bucket(tenant_id).events.values())
        if execution_id is not None:
            _require_tenant(tenant_id, execution_id)
            items = [item for item in items if item.execution_id == execution_id]
        return items

    def put_user(self, user: EnterpriseUser) -> EnterpriseUser:
        bucket = self._bucket(user.tenant_id)
        for existing in bucket.users.values():
            if existing.username == user.username and existing.id != user.id:
                raise EnterpriseSchemaError(
                    f"username {user.username!r} already exists in tenant"
                )
        bucket.users[user.id.value] = user
        return user

    def get_user(self, tenant_id: TenantId, user_id: UserId) -> EnterpriseUser:
        _require_tenant(tenant_id, user_id)
        try:
            return self._bucket(tenant_id).users[user_id.value]
        except KeyError as exc:
            raise EntityNotFoundError(f"unknown user {user_id.value}") from exc

    def list_users(self, tenant_id: TenantId) -> list[EnterpriseUser]:
        return list(self._bucket(tenant_id).users.values())

    def append_audit(self, event: AuditEvent) -> AuditEvent:
        if event.tenant_id is None:
            raise EnterpriseSchemaError("audit event requires a tenant_id")
        self._bucket(event.tenant_id).audit.append(event)
        return event

    def list_audit(self, tenant_id: TenantId) -> list[AuditEvent]:
        return list(self._bucket(tenant_id).audit)

    def export_audit(self, tenant_id: TenantId) -> list[dict]:
        return [item.to_dict() for item in self.list_audit(tenant_id)]

    def update_audit(self, event_id: str, **changes: object) -> None:
        from matraix.enterprise.audit import refuse_audit_mutation

        refuse_audit_mutation()

    def delete_audit(self, event_id: str) -> None:
        from matraix.enterprise.audit import refuse_audit_mutation

        refuse_audit_mutation()

    def put_governance_review(self, review: GovernanceReview) -> GovernanceReview:
        self._bucket(review.tenant_id).reviews[review.id] = review
        return review

    def list_governance_reviews(self, tenant_id: TenantId) -> list[GovernanceReview]:
        return list(self._bucket(tenant_id).reviews.values())

    def close(self) -> None:
        return None


class EnterpriseStore(InMemoryEnterpriseStore):
    """Stable name for the Phase 0 in-memory store (dev / test fallback)."""
