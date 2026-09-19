"""AgentTwin Enterprise domain model (Phase 0–4).

This package is additive. It does not replace Harbor jobs, Playground, or the
existing 1,290-dimension persona YAML schema. Enterprise fields are optional
simulation parameters — not a claim of psychological equivalence to humans.

Public surface: typed IDs, core entities, policy enums and gateway, tenant-bound
repositories (in-memory default, SQLite optional), ``/api/v1``, org-graph
edges, a population-shape builder, experiment launch records mapped onto
Harbor job YAML (not a replacement for ``harbor.Job``), and a
provider-independent model gateway beside LiteLLM.
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
    ApprovalRequiredError,
    CrossTenantAccessError,
    EnterpriseSchemaError,
    EntityNotFoundError,
    PolicyDeniedError,
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
from matraix.enterprise.model_gateway import (
    DEFAULT_MODEL_CATALOG,
    ModelCapabilities,
    ModelGateway,
    ModelPolicy,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelRoute,
    ModelUsage,
    catalog_for_policy,
    complete_model,
    default_model_policy,
    resolve_model_policy,
    route_model,
)
from matraix.enterprise.population_builder import (
    GenerationBackend,
    PopulationDeclaration,
    PopulationSegment,
    build_population_declaration,
)
from matraix.enterprise.policy import (
    PolicyDecision,
    PolicyEvaluation,
    PolicyRequest,
    default_simulation_decision,
    evaluate_policy,
)
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
    "ApprovalRequiredError",
    "CohortId",
    "CrossTenantAccessError",
    "DataClassification",
    "DEFAULT_MODEL_CATALOG",
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
    "evaluate_policy",
    "ExecutionBudget",
    "ExecutionId",
    "Experiment",
    "ExperimentGovernance",
    "ExperimentId",
    "ExperimentKind",
    "GenerationBackend",
    "InMemoryEnterpriseStore",
    "map_experiment_to_harbor_job",
    "ModelCapabilities",
    "ModelGateway",
    "ModelId",
    "ModelPolicy",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "ModelRoute",
    "ModelUsage",
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
    "PolicyDeniedError",
    "PolicyEvaluation",
    "PolicyId",
    "PolicyRequest",
    "Population",
    "PopulationDeclaration",
    "PopulationId",
    "PopulationSegment",
    "build_population_declaration",
    "catalog_for_policy",
    "complete_model",
    "create_tenant_with_default_org",
    "default_model_policy",
    "default_simulation_decision",
    "resolve_model_policy",
    "route_model",
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
