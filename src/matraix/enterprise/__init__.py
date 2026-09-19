"""AgentTwin Enterprise domain model (Phase 0 foundation).

This package is additive. It does not replace Harbor jobs, Playground, or the
existing 1,290-dimension persona YAML schema. Enterprise fields are optional
simulation parameters — not a claim of psychological equivalence to humans.

Public surface is intentionally small: typed IDs, core entities, policy enums,
and tenant-bound repository contracts.
"""

from matraix.enterprise.entities import (
    DataClassification,
    Department,
    EnterpriseDimensions,
    EnterprisePersona,
    ExecutionBudget,
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
from matraix.enterprise.ids import (
    CohortId,
    DepartmentId,
    EntityId,
    ExecutionId,
    ExperimentId,
    ModelId,
    ObservationId,
    OrganizationId,
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
from matraix.enterprise.policy import PolicyDecision, PolicyRequest
from matraix.enterprise.repositories import (
    EnterpriseStore,
    InMemoryEnterpriseStore,
)

__all__ = [
    "CohortId",
    "CrossTenantAccessError",
    "DataClassification",
    "Department",
    "DepartmentId",
    "EnterpriseDimensions",
    "EnterprisePersona",
    "EnterpriseSchemaError",
    "EnterpriseStore",
    "EntityId",
    "EntityNotFoundError",
    "ExecutionBudget",
    "ExecutionId",
    "Experiment",
    "ExperimentId",
    "InMemoryEnterpriseStore",
    "ModelId",
    "ObservationId",
    "Organization",
    "OrganizationId",
    "PersonaId",
    "PolicyDecision",
    "PolicyId",
    "PolicyRequest",
    "Population",
    "PopulationId",
    "ScenarioId",
    "TaskId",
    "Team",
    "TeamId",
    "Tenant",
    "TenantId",
    "UserId",
    "new_id",
]
