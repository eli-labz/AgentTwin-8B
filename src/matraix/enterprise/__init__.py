"""AgentTwin Enterprise domain model (Phase 0–3).

This package is additive. It does not replace Harbor jobs, Playground, or the
existing 1,290-dimension persona YAML schema. Enterprise fields are optional
simulation parameters — not a claim of psychological equivalence to humans.

Public surface: typed IDs, core entities, policy enums, tenant-bound
repositories (in-memory default, SQLite optional), ``/api/v1``, org-graph
edges, a population-shape builder, and experiment launch records mapped onto
Harbor job YAML (not a replacement for ``harbor.Job``).
"""

from matraix.enterprise.entities import (
    DataClassification,
    Department,
    EnterpriseDimensions,
    EnterprisePersona,
    ExecutionBudget,
    EnterpriseExperiment,
    Experiment,
    ExperimentGovernance,
    ExperimentKind,
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
from matraix.enterprise.graph import OrgEdge, OrgNodeKind, OrgRelation
from matraix.enterprise.ids import (
    CohortId,
    DepartmentId,
    EntityId,
    ExecutionId,
    ExperimentId,
    ModelId,
    ObservationId,
    OrganizationId,
    OrgEdgeId,
    PersonaId,
    PolicyId,
    PopulationId,
    ScenarioId,
    TaskId,
    TeamId,
    TenantId,
    UserId,
    new_id,
)
from matraix.enterprise.experiment_launch import (
    CostEstimate,
    estimate_experiment_cost,
    map_experiment_to_harbor_job,
)
from matraix.enterprise.population_builder import (
    GenerationBackend,
    PopulationDeclaration,
    PopulationSegment,
    build_population_declaration,
)
from matraix.enterprise.policy import PolicyDecision, PolicyRequest
from matraix.enterprise.repositories import (
    EnterpriseRepository,
    EnterpriseStore,
    InMemoryEnterpriseStore,
)
from matraix.enterprise.sqlite_store import SqliteEnterpriseStore
from matraix.enterprise.store import (
    create_tenant_with_default_org,
    open_enterprise_store,
)

__all__ = [
    "CohortId",
    "CrossTenantAccessError",
    "DataClassification",
    "Department",
    "DepartmentId",
    "CostEstimate",
    "EnterpriseDimensions",
    "EnterpriseExperiment",
    "EnterprisePersona",
    "EnterpriseSchemaError",
    "EnterpriseRepository",
    "EnterpriseStore",
    "EntityId",
    "EntityNotFoundError",
    "estimate_experiment_cost",
    "ExecutionBudget",
    "ExecutionId",
    "Experiment",
    "ExperimentGovernance",
    "ExperimentId",
    "ExperimentKind",
    "GenerationBackend",
    "InMemoryEnterpriseStore",
    "map_experiment_to_harbor_job",
    "ModelId",
    "ObservationId",
    "open_enterprise_store",
    "OrgEdge",
    "OrgEdgeId",
    "OrgNodeKind",
    "OrgRelation",
    "Organization",
    "OrganizationId",
    "PersonaId",
    "PolicyDecision",
    "PolicyId",
    "PolicyRequest",
    "Population",
    "PopulationDeclaration",
    "PopulationId",
    "PopulationSegment",
    "build_population_declaration",
    "create_tenant_with_default_org",
    "ScenarioId",
    "SqliteEnterpriseStore",
    "TaskId",
    "Team",
    "TeamId",
    "Tenant",
    "TenantId",
    "UserId",
    "new_id",
]
