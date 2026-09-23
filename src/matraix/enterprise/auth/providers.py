"""Pluggable authentication providers.

Resolution order in :class:`ChainAuthProvider`: each provider inspects the
request headers and returns a :class:`Principal` or ``None``; the first hit
wins. When *no* provider is configured at all, :class:`DevOpenAuthProvider`
keeps the historic local-development behaviour (open API) and logs a warning
once. Production deployments must configure at least one of:

* ``MATRIX_ENTERPRISE_API_TOKEN`` — single shared platform-admin token
  (backward compatible with Phase 1);
* ``MATRIX_ENTERPRISE_AUTH_TOKENS`` — JSON map ``token -> principal spec``
  (``{"subject", "tenant_id", "roles", "kind", "platform_admin"}``);
* service accounts stored in the enterprise store (hashed tokens);
* OIDC: ``MATRIX_ENTERPRISE_OIDC_ISSUER``, ``MATRIX_ENTERPRISE_OIDC_AUDIENCE``
  and either ``MATRIX_ENTERPRISE_OIDC_JWKS_JSON`` or
  ``MATRIX_ENTERPRISE_OIDC_JWKS_URL`` (requires ``pyjwt[crypto]``), with
  ``MATRIX_ENTERPRISE_OIDC_TENANT_CLAIM`` (default ``tenant_id``) and
  ``MATRIX_ENTERPRISE_OIDC_ROLES_CLAIM`` (default ``roles``).

Secrets are read from :mod:`matraix.enterprise.secrets`, never logged.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import timezone
from typing import Any, Mapping, Protocol

from matraix.enterprise.auth.principal import Principal
from matraix.enterprise.auth.roles import AuthenticationError, role_from_string
from matraix.enterprise.auth.tokens import hash_token
from matraix.enterprise.domain.base import coerce_tenant_id, utcnow
from matraix.enterprise.domain.models import PrincipalKind, RoleName, ServiceAccount
from matraix.enterprise.ids import EntityKind
from matraix.enterprise.records import RecordQuery, RecordStore
from matraix.enterprise.secrets import SecretProvider, default_secret_provider

__all__ = [
    "AuthProvider",
    "ChainAuthProvider",
    "DevOpenAuthProvider",
    "LocalBearerAuthProvider",
    "OidcJwtAuthProvider",
    "OidcConfig",
    "ServiceAccountAuthProvider",
    "bearer_token",
    "build_auth_provider_from_env",
]

logger = logging.getLogger("matraix.enterprise.auth")

API_TOKEN_ENV = "MATRIX_ENTERPRISE_API_TOKEN"
AUTH_TOKENS_ENV = "MATRIX_ENTERPRISE_AUTH_TOKENS"
AUTH_MODE_ENV = "MATRIX_ENTERPRISE_AUTH_MODE"


def bearer_token(headers: Mapping[str, str]) -> str | None:
    header = headers.get("authorization") or headers.get("Authorization") or ""
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        return token or None
    return None


class AuthProvider(Protocol):
    name: str

    def authenticate(self, headers: Mapping[str, str]) -> Principal | None: ...


def _principal_from_spec(spec: Mapping[str, Any], *, provider: str) -> Principal:
    roles = frozenset(role_from_string(item) for item in spec.get("roles", []))
    kind = PrincipalKind(str(spec.get("kind", "user")))
    tenant = spec.get("tenant_id")
    return Principal(
        subject=str(spec.get("subject") or "unknown"),
        kind=kind,
        tenant_id=coerce_tenant_id(tenant) if tenant else None,
        roles=roles,
        platform_admin=bool(spec.get("platform_admin", False)),
        display_name=spec.get("display_name"),
        provider=provider,
    )


@dataclass
class LocalBearerAuthProvider:
    """Static token map (dev / CI / bootstrap)."""

    tokens: dict[str, Principal] = field(default_factory=dict)
    name: str = "local-bearer"

    @classmethod
    def from_secret_provider(cls, secrets: SecretProvider) -> "LocalBearerAuthProvider":
        tokens: dict[str, Principal] = {}
        shared = secrets.get(API_TOKEN_ENV)
        if shared:
            tokens[shared] = Principal(
                subject="platform-admin",
                kind=PrincipalKind.SERVICE_ACCOUNT,
                tenant_id=None,
                roles=frozenset({RoleName.ADMIN}),
                platform_admin=True,
                provider="local-bearer",
            )
        raw_map = secrets.get(AUTH_TOKENS_ENV)
        if raw_map:
            try:
                parsed = json.loads(raw_map)
            except json.JSONDecodeError as exc:
                raise AuthenticationError(f"{AUTH_TOKENS_ENV} is not valid JSON") from exc
            if not isinstance(parsed, dict):
                raise AuthenticationError(f"{AUTH_TOKENS_ENV} must be a JSON object")
            for token, spec in parsed.items():
                if not isinstance(spec, dict):
                    raise AuthenticationError(f"{AUTH_TOKENS_ENV} entries must be objects")
                tokens[str(token)] = _principal_from_spec(spec, provider="local-bearer")
        return cls(tokens=tokens)

    def authenticate(self, headers: Mapping[str, str]) -> Principal | None:
        token = bearer_token(headers)
        if not token:
            return None
        return self.tokens.get(token)


@dataclass
class ServiceAccountAuthProvider:
    """Looks up hashed service-account tokens in the enterprise store.

    Tokens embed no tenant, so the provider scans every tenant's service
    accounts by hash (indexed lookup in the SQL stores via ``where``).
    """

    store: RecordStore
    tenant_ids: Any  # callable returning list[str]
    name: str = "service-account"

    def authenticate(self, headers: Mapping[str, str]) -> Principal | None:
        token = bearer_token(headers)
        if not token or not token.startswith("atsk_"):
            return None
        digest = hash_token(token)
        for tenant_id in self.tenant_ids():
            matches = self.store.list_records(
                tenant_id,
                EntityKind.SERVICE_ACCOUNT,
                ServiceAccount,
                RecordQuery(where={"token_hash": digest}, limit=1),
            )
            if not matches:
                continue
            account = matches[0]
            if account.status != "active":
                raise AuthenticationError("service account is disabled")
            if account.expires_at is not None:
                expires = account.expires_at
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires <= utcnow():
                    raise AuthenticationError("service account token has expired")
            return Principal(
                subject=account.id,
                kind=PrincipalKind.SERVICE_ACCOUNT,
                tenant_id=account.tenant_id,
                roles=frozenset(account.roles),
                display_name=account.name,
                provider=self.name,
            )
        return None


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    audience: str
    jwks: dict[str, Any] | None = None
    jwks_url: str | None = None
    tenant_claim: str = "tenant_id"
    roles_claim: str = "roles"
    subject_claim: str = "sub"
    platform_admin_claim: str | None = None
    algorithms: tuple[str, ...] = ("RS256", "ES256")
    leeway_seconds: int = 30
    hs256_secret: str | None = None

    @classmethod
    def from_secret_provider(cls, secrets: SecretProvider) -> "OidcConfig | None":
        issuer = secrets.get("MATRIX_ENTERPRISE_OIDC_ISSUER")
        audience = secrets.get("MATRIX_ENTERPRISE_OIDC_AUDIENCE")
        if not issuer or not audience:
            return None
        jwks_json = secrets.get("MATRIX_ENTERPRISE_OIDC_JWKS_JSON")
        jwks_url = secrets.get("MATRIX_ENTERPRISE_OIDC_JWKS_URL")
        hs_secret = secrets.get("MATRIX_ENTERPRISE_OIDC_HS256_SECRET")
        algorithms: tuple[str, ...] = ("RS256", "ES256")
        raw_algs = os.environ.get("MATRIX_ENTERPRISE_OIDC_ALGORITHMS")
        if raw_algs:
            algorithms = tuple(item.strip() for item in raw_algs.split(",") if item.strip())
        if not jwks_json and not jwks_url and not hs_secret:
            raise AuthenticationError(
                "OIDC needs MATRIX_ENTERPRISE_OIDC_JWKS_JSON, _JWKS_URL or _HS256_SECRET"
            )
        return cls(
            issuer=issuer,
            audience=audience,
            jwks=json.loads(jwks_json) if jwks_json else None,
            jwks_url=jwks_url,
            tenant_claim=os.environ.get("MATRIX_ENTERPRISE_OIDC_TENANT_CLAIM", "tenant_id"),
            roles_claim=os.environ.get("MATRIX_ENTERPRISE_OIDC_ROLES_CLAIM", "roles"),
            platform_admin_claim=os.environ.get("MATRIX_ENTERPRISE_OIDC_PLATFORM_ADMIN_CLAIM") or None,
            algorithms=algorithms,
            hs256_secret=hs_secret,
        )


class OidcJwtAuthProvider:
    """Verifies JWT bearer tokens (signature, issuer, audience, expiry)."""

    name = "oidc"

    def __init__(self, config: OidcConfig) -> None:
        self.config = config
        try:
            import jwt  # noqa: F401
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError("OIDC auth requires pyjwt: pip install 'matraix[oidc]'") from exc
        self._jwks_client: Any = None

    def _key_for(self, token: str) -> Any:
        import jwt

        header = jwt.get_unverified_header(token)
        alg = header.get("alg")
        if alg == "HS256":
            if not self.config.hs256_secret:
                raise AuthenticationError("HS256 token but no shared secret configured")
            return self.config.hs256_secret
        kid = header.get("kid")
        if self.config.jwks is not None:
            for key in self.config.jwks.get("keys", []):
                if kid is None or key.get("kid") == kid:
                    return jwt.PyJWK(key).key
            raise AuthenticationError("no matching JWK for token")
        if self.config.jwks_url:
            if self._jwks_client is None:
                self._jwks_client = jwt.PyJWKClient(self.config.jwks_url)
            return self._jwks_client.get_signing_key_from_jwt(token).key
        raise AuthenticationError("no JWKS configured")

    def authenticate(self, headers: Mapping[str, str]) -> Principal | None:
        token = bearer_token(headers)
        if not token or token.count(".") != 2:
            return None
        import jwt

        try:
            key = self._key_for(token)
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(self.config.algorithms) + (["HS256"] if self.config.hs256_secret else []),
                audience=self.config.audience,
                issuer=self.config.issuer,
                leeway=self.config.leeway_seconds,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"invalid OIDC token: {exc.__class__.__name__}") from exc
        roles_raw = claims.get(self.config.roles_claim, [])
        if isinstance(roles_raw, str):
            roles_raw = [item for item in roles_raw.replace(",", " ").split() if item]
        roles = frozenset(role_from_string(item) for item in roles_raw)
        tenant = claims.get(self.config.tenant_claim)
        platform_admin = False
        if self.config.platform_admin_claim:
            platform_admin = bool(claims.get(self.config.platform_admin_claim))
        return Principal(
            subject=str(claims.get(self.config.subject_claim, "unknown")),
            kind=PrincipalKind.USER,
            tenant_id=coerce_tenant_id(tenant) if tenant else None,
            roles=roles,
            platform_admin=platform_admin,
            display_name=claims.get("name") or claims.get("email"),
            provider=self.name,
            claims={
                key: str(value)
                for key, value in claims.items()
                if key in {"email", "name", "preferred_username"} and isinstance(value, str)
            },
        )


class DevOpenAuthProvider:
    """No credentials configured: every request is a platform admin.

    Matches the pre-Phase-1 behaviour of the open local API. Emits a single
    warning so operators notice. Disabled automatically once any real provider
    is configured, and refused when ``MATRIX_ENTERPRISE_AUTH_MODE=strict``.
    """

    name = "dev-open"
    _warned = False

    def authenticate(self, headers: Mapping[str, str]) -> Principal | None:
        if not DevOpenAuthProvider._warned:
            logger.warning(
                "enterprise API running without authentication (dev-open). "
                "Configure MATRIX_ENTERPRISE_API_TOKEN, AUTH_TOKENS or OIDC for any shared deployment."
            )
            DevOpenAuthProvider._warned = True
        tenant = headers.get("x-tenant-id") or headers.get("X-Tenant-Id")
        return Principal(
            subject="dev-admin",
            kind=PrincipalKind.USER,
            tenant_id=coerce_tenant_id(tenant) if tenant else None,
            roles=frozenset({RoleName.ADMIN}),
            platform_admin=True,
            provider=self.name,
        )


@dataclass
class ChainAuthProvider:
    providers: list[AuthProvider]
    name: str = "chain"

    def authenticate(self, headers: Mapping[str, str]) -> Principal | None:
        for provider in self.providers:
            principal = provider.authenticate(headers)
            if principal is not None:
                return principal
        return None

    @property
    def open(self) -> bool:
        return any(isinstance(item, DevOpenAuthProvider) for item in self.providers)


def build_auth_provider_from_env(
    *,
    store: RecordStore | None = None,
    tenant_ids: Any = None,
    secrets: SecretProvider | None = None,
) -> ChainAuthProvider:
    """Compose providers from the environment. Fails closed in strict mode."""
    secrets = secrets or default_secret_provider()
    providers: list[AuthProvider] = []
    local = LocalBearerAuthProvider.from_secret_provider(secrets)
    if local.tokens:
        providers.append(local)
    if store is not None and tenant_ids is not None:
        providers.append(ServiceAccountAuthProvider(store=store, tenant_ids=tenant_ids))
    oidc = OidcConfig.from_secret_provider(secrets)
    if oidc is not None:
        providers.append(OidcJwtAuthProvider(oidc))
    strict = os.environ.get(AUTH_MODE_ENV, "").strip().lower() == "strict"
    configured = bool(local.tokens) or oidc is not None
    if not configured:
        if strict:
            raise AuthenticationError(
                "MATRIX_ENTERPRISE_AUTH_MODE=strict but no authentication provider is configured"
            )
        providers.append(DevOpenAuthProvider())
    return ChainAuthProvider(providers=providers)
