"""Pluggable authentication and tenant-aware RBAC."""

from matraix.enterprise.auth.principal import Principal, authorize, require_tenant_scope
from matraix.enterprise.auth.providers import (
    AuthProvider,
    ChainAuthProvider,
    DevOpenAuthProvider,
    LocalBearerAuthProvider,
    OidcConfig,
    OidcJwtAuthProvider,
    ServiceAccountAuthProvider,
    bearer_token,
    build_auth_provider_from_env,
)
from matraix.enterprise.auth.roles import (
    ROLE_PERMISSIONS,
    AuthenticationError,
    AuthorizationError,
    Permission,
    permissions_for,
    role_from_string,
)
from matraix.enterprise.auth.tokens import generate_token, hash_token, token_prefix, verify_token

__all__ = [
    "AuthProvider",
    "AuthenticationError",
    "AuthorizationError",
    "ChainAuthProvider",
    "DevOpenAuthProvider",
    "LocalBearerAuthProvider",
    "OidcConfig",
    "OidcJwtAuthProvider",
    "Permission",
    "Principal",
    "ROLE_PERMISSIONS",
    "ServiceAccountAuthProvider",
    "authorize",
    "bearer_token",
    "build_auth_provider_from_env",
    "generate_token",
    "hash_token",
    "permissions_for",
    "require_tenant_scope",
    "role_from_string",
    "token_prefix",
    "verify_token",
]
