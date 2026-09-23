"""Roles, permissions and the authorization check.

Least-privilege matrix for the five enterprise roles. Permissions are
strings of the form ``<resource>.<verb>`` so policies, audit rows and the API
can name them without importing enums.
"""

from __future__ import annotations

from enum import Enum
from typing import Iterable

from matraix.enterprise.domain.models import RoleName
from matraix.enterprise.errors import EnterpriseError

__all__ = [
    "AuthenticationError",
    "AuthorizationError",
    "Permission",
    "ROLE_PERMISSIONS",
    "permissions_for",
    "role_from_string",
]


class AuthenticationError(EnterpriseError, PermissionError):
    """No valid principal could be established (HTTP 401)."""


class AuthorizationError(EnterpriseError, PermissionError):
    """The principal lacks a permission or acts outside its tenant (HTTP 403)."""


class Permission(str, Enum):
    TENANT_READ = "tenant.read"
    TENANT_MANAGE = "tenant.manage"
    ORGANIZATION_READ = "organization.read"
    ORGANIZATION_WRITE = "organization.write"
    IDENTITY_READ = "identity.read"
    IDENTITY_WRITE = "identity.write"
    POPULATION_READ = "population.read"
    POPULATION_WRITE = "population.write"
    POPULATION_MATERIALIZE = "population.materialize"
    GRAPH_READ = "graph.read"
    GRAPH_WRITE = "graph.write"
    EXPERIMENT_READ = "experiment.read"
    EXPERIMENT_WRITE = "experiment.write"
    EXPERIMENT_LAUNCH = "experiment.launch"
    EXPERIMENT_APPROVE = "experiment.approve"
    RUN_READ = "run.read"
    RUN_CONTROL = "run.control"
    POLICY_READ = "policy.read"
    POLICY_WRITE = "policy.write"
    BUDGET_READ = "budget.read"
    BUDGET_WRITE = "budget.write"
    MODEL_READ = "model.read"
    MODEL_WRITE = "model.write"
    METRIC_READ = "metric.read"
    REPORT_READ = "report.read"
    ARTIFACT_READ = "artifact.read"
    AUDIT_READ = "audit.read"
    WORKER_EXECUTE = "worker.execute"


_VIEWER = {
    Permission.TENANT_READ,
    Permission.ORGANIZATION_READ,
    Permission.POPULATION_READ,
    Permission.GRAPH_READ,
    Permission.EXPERIMENT_READ,
    Permission.RUN_READ,
    Permission.POLICY_READ,
    Permission.BUDGET_READ,
    Permission.MODEL_READ,
    Permission.METRIC_READ,
    Permission.REPORT_READ,
}

_ANALYST = _VIEWER | {Permission.ARTIFACT_READ}

_RESEARCHER = _ANALYST | {
    Permission.POPULATION_WRITE,
    Permission.POPULATION_MATERIALIZE,
    Permission.GRAPH_WRITE,
    Permission.EXPERIMENT_WRITE,
    Permission.EXPERIMENT_LAUNCH,
}

_OPERATOR = _RESEARCHER | {
    Permission.RUN_CONTROL,
    Permission.EXPERIMENT_APPROVE,
    Permission.MODEL_WRITE,
    Permission.BUDGET_WRITE,
    Permission.POLICY_WRITE,
    Permission.WORKER_EXECUTE,
    Permission.AUDIT_READ,
}

_ADMIN = _OPERATOR | {
    Permission.TENANT_MANAGE,
    Permission.ORGANIZATION_WRITE,
    Permission.IDENTITY_READ,
    Permission.IDENTITY_WRITE,
}

ROLE_PERMISSIONS: dict[RoleName, frozenset[Permission]] = {
    RoleName.VIEWER: frozenset(_VIEWER),
    RoleName.ANALYST: frozenset(_ANALYST),
    RoleName.RESEARCHER: frozenset(_RESEARCHER),
    RoleName.OPERATOR: frozenset(_OPERATOR),
    RoleName.ADMIN: frozenset(_ADMIN),
}


def role_from_string(value: str) -> RoleName:
    try:
        return RoleName(str(value).strip().lower())
    except ValueError as exc:
        raise EnterpriseError(
            f"unknown role {value!r}; expected one of " + ", ".join(item.value for item in RoleName)
        ) from exc


def permissions_for(roles: Iterable[RoleName | str]) -> frozenset[Permission]:
    granted: set[Permission] = set()
    for role in roles:
        resolved = role if isinstance(role, RoleName) else role_from_string(role)
        granted |= ROLE_PERMISSIONS[resolved]
    return frozenset(granted)
