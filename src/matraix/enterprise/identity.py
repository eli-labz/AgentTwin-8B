"""RBAC roles, principals, and SCIM-shaped enterprise users.

OIDC/SSO maps claims onto :class:`Principal`. No IdP is required at runtime;
tokens are validated locally when a dev HMAC or issuer/audience is configured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable

from matraix.enterprise.errors import EnterpriseSchemaError
from matraix.enterprise.ids import TenantId, UserId, new_id, EntityKind


class Role(str, Enum):
    PLATFORM_ADMIN = "platform_admin"
    TENANT_ADMIN = "tenant_admin"
    SIMULATION_ADMIN = "simulation_admin"
    RESEARCHER = "researcher"
    EVALUATOR = "evaluator"
    DEVELOPER = "developer"
    AUDITOR = "auditor"
    VIEWER = "viewer"


class Permission(str, Enum):
    TENANT_READ = "tenant:read"
    TENANT_WRITE = "tenant:write"
    ORG_READ = "org:read"
    ORG_WRITE = "org:write"
    POPULATION_READ = "population:read"
    POPULATION_WRITE = "population:write"
    PERSONA_READ = "persona:read"
    PERSONA_WRITE = "persona:write"
    EXPERIMENT_READ = "experiment:read"
    EXPERIMENT_WRITE = "experiment:write"
    EXPERIMENT_EXECUTE = "experiment:execute"
    POLICY_READ = "policy:read"
    POLICY_WRITE = "policy:write"
    MODEL_READ = "model:read"
    MODEL_COMPLETE = "model:complete"
    EXECUTION_READ = "execution:read"
    REPORT_READ = "report:read"
    AUDIT_READ = "audit:read"
    GOVERNANCE_READ = "governance:read"
    GOVERNANCE_WRITE = "governance:write"
    IDENTITY_READ = "identity:read"
    IDENTITY_WRITE = "identity:write"


_READ = (
    Permission.TENANT_READ,
    Permission.ORG_READ,
    Permission.POPULATION_READ,
    Permission.PERSONA_READ,
    Permission.EXPERIMENT_READ,
    Permission.POLICY_READ,
    Permission.MODEL_READ,
    Permission.EXECUTION_READ,
    Permission.REPORT_READ,
    Permission.GOVERNANCE_READ,
    Permission.IDENTITY_READ,
)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.PLATFORM_ADMIN: frozenset(Permission),
    Role.TENANT_ADMIN: frozenset(Permission) - frozenset(),
    Role.SIMULATION_ADMIN: frozenset(
        _READ
        + (
            Permission.EXPERIMENT_WRITE,
            Permission.EXPERIMENT_EXECUTE,
            Permission.POLICY_WRITE,
            Permission.MODEL_COMPLETE,
            Permission.GOVERNANCE_WRITE,
        )
    ),
    Role.RESEARCHER: frozenset(
        _READ
        + (
            Permission.POPULATION_WRITE,
            Permission.PERSONA_WRITE,
            Permission.EXPERIMENT_WRITE,
            Permission.EXPERIMENT_EXECUTE,
            Permission.REPORT_READ,
        )
    ),
    Role.DEVELOPER: frozenset(
        _READ
        + (
            Permission.EXPERIMENT_WRITE,
            Permission.EXPERIMENT_EXECUTE,
            Permission.MODEL_COMPLETE,
            Permission.POLICY_READ,
        )
    ),
    Role.EVALUATOR: frozenset(
        (
            Permission.TENANT_READ,
            Permission.EXPERIMENT_READ,
            Permission.EXECUTION_READ,
            Permission.REPORT_READ,
            Permission.GOVERNANCE_READ,
        )
    ),
    Role.AUDITOR: frozenset(
        (
            Permission.TENANT_READ,
            Permission.EXECUTION_READ,
            Permission.REPORT_READ,
            Permission.AUDIT_READ,
            Permission.GOVERNANCE_READ,
            Permission.IDENTITY_READ,
        )
    ),
    Role.VIEWER: frozenset(_READ),
}


def permissions_for(roles: Iterable[Role | str]) -> frozenset[Permission]:
    granted: set[Permission] = set()
    for role in roles:
        resolved = role if isinstance(role, Role) else Role(str(role))
        granted.update(ROLE_PERMISSIONS.get(resolved, ()))
    return frozenset(granted)


def parse_roles(raw: Iterable[Role | str] | None) -> tuple[Role, ...]:
    roles: list[Role] = []
    for item in raw or ():
        if isinstance(item, Role):
            roles.append(item)
            continue
        text = str(item).strip().lower().replace("-", "_")
        if text.startswith("role."):
            text = text.split(".", 1)[1]
        if not text:
            continue
        roles.append(Role(text))
    return tuple(roles)


@dataclass(frozen=True, slots=True)
class EnterpriseUser:
    id: UserId
    tenant_id: TenantId
    username: str
    email: str | None = None
    external_id: str | None = None
    roles: tuple[Role, ...] = (Role.VIEWER,)
    active: bool = True
    attributes: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("user id tenant mismatch")
        name = str(self.username or "").strip()
        if not name:
            raise EnterpriseSchemaError("username is required")
        object.__setattr__(self, "username", name)
        object.__setattr__(self, "roles", parse_roles(self.roles) or (Role.VIEWER,))
        object.__setattr__(self, "attributes", dict(self.attributes or {}))
        if not self.created_at:
            object.__setattr__(
                self,
                "created_at",
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id.value,
            "tenant_id": self.tenant_id.value,
            "username": self.username,
            "email": self.email,
            "external_id": self.external_id,
            "roles": [role.value for role in self.roles],
            "active": self.active,
            "attributes": dict(self.attributes),
            "created_at": self.created_at,
        }

    def to_scim(self) -> dict[str, Any]:
        return {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "id": self.id.value,
            "userName": self.username,
            "active": self.active,
            "externalId": self.external_id,
            "emails": ([{"value": self.email, "primary": True}] if self.email else []),
            "roles": [{"value": role.value} for role in self.roles],
            "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User": {
                "tenant_id": self.tenant_id.value,
            },
        }


def user_from_scim(tenant_id: TenantId, payload: dict[str, Any]) -> EnterpriseUser:
    username = str(payload.get("userName") or payload.get("username") or "").strip()
    emails = payload.get("emails") or []
    email = None
    if isinstance(emails, list) and emails:
        first = emails[0]
        email = first.get("value") if isinstance(first, dict) else str(first)
    elif payload.get("email"):
        email = str(payload["email"])
    raw_roles = []
    for item in payload.get("roles") or []:
        if isinstance(item, dict):
            raw_roles.append(str(item.get("value") or item.get("display") or ""))
        else:
            raw_roles.append(str(item))
    body = str(payload.get("id") or "").strip() or new_id(EntityKind.USER)
    return EnterpriseUser(
        id=UserId(tenant_id, body),
        tenant_id=tenant_id,
        username=username,
        email=email,
        external_id=str(payload.get("externalId") or payload.get("external_id") or "")
        or None,
        roles=parse_roles(raw_roles) or (Role.VIEWER,),
        active=bool(payload.get("active", True)),
        attributes=dict(payload.get("attributes") or {}),
    )


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    source: str
    tenant_id: TenantId | None = None
    roles: tuple[Role, ...] = ()
    email: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def anonymous(self) -> bool:
        return self.source == "anonymous"

    def has(self, permission: Permission) -> bool:
        return permission in permissions_for(self.roles)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "source": self.source,
            "tenant_id": self.tenant_id.value if self.tenant_id else None,
            "roles": [role.value for role in self.roles],
            "email": self.email,
            "attributes": dict(self.attributes),
        }


ANONYMOUS = Principal(subject="anonymous", source="anonymous")


def service_principal(*, subject: str = "api-token") -> Principal:
    return Principal(
        subject=subject,
        source="bearer_token",
        roles=(Role.PLATFORM_ADMIN,),
    )
