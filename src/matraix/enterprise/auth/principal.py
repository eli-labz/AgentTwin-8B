"""Authenticated principal and tenant-scoped authorization."""

from __future__ import annotations

from dataclasses import dataclass, field

from matraix.enterprise.auth.roles import (
    AuthorizationError,
    Permission,
    permissions_for,
)
from matraix.enterprise.domain.base import coerce_tenant_id
from matraix.enterprise.domain.models import PrincipalKind, RoleName

__all__ = ["Principal", "authorize", "require_tenant_scope"]


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is acting. ``platform_admin`` may cross tenants (bootstrap / ops)."""

    subject: str
    kind: PrincipalKind
    tenant_id: str | None
    roles: frozenset[RoleName] = field(default_factory=frozenset)
    platform_admin: bool = False
    display_name: str | None = None
    provider: str = "local"
    claims: dict[str, str] = field(default_factory=dict)

    @property
    def permissions(self) -> frozenset[Permission]:
        if self.platform_admin:
            return frozenset(Permission)
        return permissions_for(self.roles)

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions

    def to_dict(self) -> dict[str, object]:
        return {
            "subject": self.subject,
            "kind": self.kind.value,
            "tenant_id": self.tenant_id,
            "roles": sorted(role.value for role in self.roles),
            "platform_admin": self.platform_admin,
            "display_name": self.display_name,
            "provider": self.provider,
            "permissions": sorted(item.value for item in self.permissions),
        }


def require_tenant_scope(principal: Principal, tenant_id: str) -> str:
    """Return the canonical tenant id if ``principal`` may act inside it."""
    wanted = coerce_tenant_id(tenant_id)
    if principal.platform_admin:
        return wanted
    if principal.tenant_id is None or coerce_tenant_id(principal.tenant_id) != wanted:
        raise AuthorizationError(
            f"principal {principal.subject!r} is not a member of tenant {wanted}"
        )
    return wanted


def authorize(principal: Principal, permission: Permission, tenant_id: str | None = None) -> None:
    """Raise :class:`AuthorizationError` unless ``principal`` holds ``permission``.

    When ``tenant_id`` is given, the principal must also be scoped to it.
    """
    if tenant_id is not None:
        require_tenant_scope(principal, tenant_id)
    if not principal.has(permission):
        raise AuthorizationError(
            f"principal {principal.subject!r} lacks permission {permission.value}"
        )
