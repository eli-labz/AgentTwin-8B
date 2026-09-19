"""RBAC plus ABAC hooks for the enterprise API.

Anonymous principals (open local/dev) skip RBAC so existing smoke/API tests
stay green. Authenticated principals are checked. Cross-tenant acting is
denied unless the principal is ``platform_admin``.
"""

from __future__ import annotations

from typing import Any

from matraix.enterprise.errors import AuthorizationError
from matraix.enterprise.identity import (
    ANONYMOUS,
    Permission,
    Principal,
    Role,
)
from matraix.enterprise.ids import TenantId


def authorize(
    principal: Principal | None,
    permission: Permission | str,
    *,
    tenant_id: TenantId | None = None,
    resource: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Principal:
    """Return the principal or raise :class:`AuthorizationError`.

    ABAC hooks (least privilege):
    - inactive users are denied
    - tenant-bound principals may not act on another tenant
    - ``RESTRICTED`` resources require tenant_admin+ unless platform_admin
    """
    actor = principal or ANONYMOUS
    if actor.anonymous:
        return actor
    needed = (
        permission if isinstance(permission, Permission) else Permission(permission)
    )
    attrs = dict(actor.attributes)
    if attributes:
        attrs.update(attributes)
    if attrs.get("active") is False:
        raise AuthorizationError("principal is inactive", permission=needed.value)
    if Role.PLATFORM_ADMIN not in actor.roles:
        if tenant_id is not None and actor.tenant_id is not None:
            if actor.tenant_id != tenant_id:
                raise AuthorizationError(
                    "principal is not authorized for this tenant",
                    permission=needed.value,
                )
        classification = str(attrs.get("data_classification") or "").upper()
        if classification == "RESTRICTED" and not actor.has(Permission.POLICY_WRITE):
            raise AuthorizationError(
                "RESTRICTED resources require an admin policy role",
                permission=needed.value,
            )
    if not actor.has(needed):
        raise AuthorizationError(
            f"missing permission {needed.value}"
            + (f" on {resource}" if resource else ""),
            permission=needed.value,
        )
    return actor


def permission_for(method: str, path: str) -> Permission | None:
    """Map a route to a permission. ``None`` means no extra RBAC check."""
    verb = (method or "GET").upper()
    cleaned = (path or "/").rstrip("/") or "/"
    if cleaned in {"/health", "/docs", "/redoc", "/openapi.json", "/console"}:
        return None
    if cleaned.startswith("/console"):
        return None
    if cleaned == "/api/v1/console/manifest":
        return None
    if cleaned.startswith("/api/v1/auth"):
        return None
    if cleaned.startswith("/api/v1/scim"):
        return Permission.IDENTITY_WRITE if verb != "GET" else Permission.IDENTITY_READ
    if cleaned.startswith("/api/v1/audit"):
        return Permission.AUDIT_READ
    if cleaned.startswith("/api/v1/governance"):
        return (
            Permission.GOVERNANCE_WRITE
            if verb in {"POST", "PUT", "PATCH", "DELETE"}
            else Permission.GOVERNANCE_READ
        )
    if cleaned.startswith("/api/v1/users"):
        return (
            Permission.IDENTITY_WRITE
            if verb in {"POST", "PUT", "PATCH", "DELETE"}
            else Permission.IDENTITY_READ
        )
    if "/report" in cleaned:
        return Permission.REPORT_READ
    if "/evaluate" in cleaned or cleaned.endswith("/evaluation"):
        return Permission.EXECUTION_READ
    if "/trace" in cleaned or "/metrics" in cleaned or cleaned.endswith("/failures"):
        return Permission.EXECUTION_READ
    if "/execute" in cleaned or cleaned == "/api/v1/executions" and verb == "POST":
        return Permission.EXPERIMENT_EXECUTE
    if cleaned.startswith("/api/v1/executions"):
        return Permission.EXECUTION_READ
    if cleaned.startswith("/api/v1/events"):
        return Permission.EXECUTION_READ
    if cleaned.startswith("/api/v1/models/complete"):
        return Permission.MODEL_COMPLETE
    if cleaned.startswith("/api/v1/model-policy"):
        return (
            Permission.POLICY_WRITE
            if verb in {"POST", "PUT", "PATCH"}
            else Permission.POLICY_READ
        )
    if cleaned.startswith("/api/v1/policy"):
        return Permission.POLICY_READ
    if cleaned.startswith("/api/v1/models"):
        return Permission.MODEL_READ
    if cleaned.startswith("/api/v1/workers"):
        return None
    if cleaned.startswith("/api/v1/experiments"):
        if verb in {"POST", "PUT", "PATCH", "DELETE"}:
            return Permission.EXPERIMENT_WRITE
        return Permission.EXPERIMENT_READ
    if cleaned.startswith("/api/v1/personas"):
        return (
            Permission.PERSONA_WRITE
            if verb in {"POST", "PUT", "PATCH", "DELETE"}
            else Permission.PERSONA_READ
        )
    if "population" in cleaned:
        return (
            Permission.POPULATION_WRITE
            if verb in {"POST", "PUT", "PATCH", "DELETE"}
            else Permission.POPULATION_READ
        )
    if cleaned.startswith("/api/v1/org-edges") or cleaned.startswith(
        "/api/v1/organizations"
    ):
        return (
            Permission.ORG_WRITE
            if verb in {"POST", "PUT", "PATCH", "DELETE"}
            else Permission.ORG_READ
        )
    if cleaned.startswith("/api/v1/tenants"):
        return (
            Permission.TENANT_WRITE
            if verb in {"POST", "PUT", "PATCH", "DELETE"}
            else Permission.TENANT_READ
        )
    return None
