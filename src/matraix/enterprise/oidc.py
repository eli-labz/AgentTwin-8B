"""OIDC/SSO token patterns without a live IdP.

Production deployments point ``MATRIX_ENTERPRISE_OIDC_ISSUER`` /
``MATRIX_ENTERPRISE_OIDC_AUDIENCE`` / ``MATRIX_ENTERPRISE_OIDC_JWKS_URI``
at their provider. CI and local tests mint HS256 tokens with
``MATRIX_ENTERPRISE_OIDC_DEV_SECRET`` (environment only — never committed).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from matraix.enterprise.errors import EnterpriseSchemaError
from matraix.enterprise.http_security import is_production
from matraix.enterprise.identity import Principal, parse_roles
from matraix.enterprise.ids import TenantId

OIDC_ISSUER_ENV = "MATRIX_ENTERPRISE_OIDC_ISSUER"
OIDC_AUDIENCE_ENV = "MATRIX_ENTERPRISE_OIDC_AUDIENCE"
OIDC_JWKS_ENV = "MATRIX_ENTERPRISE_OIDC_JWKS_URI"
OIDC_DEV_SECRET_ENV = "MATRIX_ENTERPRISE_OIDC_DEV_SECRET"


def oidc_issuer() -> str | None:
    value = os.environ.get(OIDC_ISSUER_ENV, "").strip()
    return value or None


def oidc_audience() -> str | None:
    value = os.environ.get(OIDC_AUDIENCE_ENV, "").strip()
    return value or None


def oidc_jwks_uri() -> str | None:
    value = os.environ.get(OIDC_JWKS_ENV, "").strip()
    return value or None


def oidc_dev_secret() -> str | None:
    value = os.environ.get(OIDC_DEV_SECRET_ENV, "").strip()
    return value or None


def oidc_configured() -> bool:
    return bool(oidc_dev_secret() or (oidc_issuer() and oidc_audience()))


def oidc_metadata() -> dict[str, Any]:
    issuer = oidc_issuer() or "https://idp.example.invalid"
    return {
        "issuer": issuer,
        "audience": oidc_audience() or "agenttwin-enterprise",
        "jwks_uri": oidc_jwks_uri() or f"{issuer.rstrip('/')}/.well-known/jwks.json",
        "authorization_endpoint": f"{issuer.rstrip('/')}/authorize",
        "token_endpoint": f"{issuer.rstrip('/')}/token",
        "end_session_endpoint": f"{issuer.rstrip('/')}/logout",
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "id_token_signing_alg_values_supported": ["RS256", "HS256"],
        "note": (
            "Pattern only. This process does not call a live IdP. "
            "Set MATRIX_ENTERPRISE_OIDC_* from the environment. "
            "Authorization Code + PKCE is the documented browser flow."
        ),
        "live_idp_required": False,
    }


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def mint_dev_jwt(
    *,
    subject: str,
    tenant_id: str | None = None,
    roles: list[str] | None = None,
    email: str | None = None,
    ttl_seconds: int = 3600,
    extra: dict[str, Any] | None = None,
) -> str:
    """Mint an HS256 JWT for tests / local SSO. Refuses production."""
    secret = oidc_dev_secret()
    if not secret:
        raise EnterpriseSchemaError(
            "MATRIX_ENTERPRISE_OIDC_DEV_SECRET is unset; will not mint tokens"
        )
    if is_production():
        raise EnterpriseSchemaError("dev JWT minting is disabled in production")
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": oidc_issuer() or "https://idp.example.invalid",
        "aud": oidc_audience() or "agenttwin-enterprise",
        "sub": subject,
        "iat": now,
        "exp": now + max(30, ttl_seconds),
    }
    if tenant_id:
        claims["tenant_id"] = tenant_id
    if roles:
        claims["roles"] = list(roles)
    if email:
        claims["email"] = email
    if extra:
        claims.update(extra)
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    signing = f"{header}.{payload}".encode()
    signature = _b64url_encode(
        hmac.new(secret.encode("utf-8"), signing, hashlib.sha256).digest()
    )
    return f"{header}.{payload}.{signature}"


def _looks_like_jwt(token: str) -> bool:
    return token.count(".") == 2


def decode_oidc_token(token: str) -> dict[str, Any]:
    if not _looks_like_jwt(token):
        raise EnterpriseSchemaError("not an OIDC JWT")
    header_b64, payload_b64, signature_b64 = token.split(".")
    header = json.loads(_b64url_decode(header_b64))
    payload = json.loads(_b64url_decode(payload_b64))
    if header.get("alg") != "HS256":
        raise EnterpriseSchemaError(
            "only HS256 local validation is wired; configure JWKS for RS256 IdPs"
        )
    secret = oidc_dev_secret()
    if not secret:
        raise EnterpriseSchemaError("OIDC HMAC secret is not configured")
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{header_b64}.{payload_b64}".encode(),
        hashlib.sha256,
    ).digest()
    actual = _b64url_decode(signature_b64)
    if not hmac.compare_digest(expected, actual):
        raise EnterpriseSchemaError("OIDC token signature mismatch")
    now = int(time.time())
    if int(payload.get("exp") or 0) < now:
        raise EnterpriseSchemaError("OIDC token expired")
    issuer = oidc_issuer()
    if issuer and payload.get("iss") != issuer:
        raise EnterpriseSchemaError("OIDC issuer mismatch")
    audience = oidc_audience()
    aud = payload.get("aud")
    if audience:
        if isinstance(aud, list):
            if audience not in aud:
                raise EnterpriseSchemaError("OIDC audience mismatch")
        elif aud != audience:
            raise EnterpriseSchemaError("OIDC audience mismatch")
    return payload


def principal_from_claims(claims: dict[str, Any]) -> Principal:
    tenant_raw = claims.get("tenant_id") or claims.get("tid")
    tenant = TenantId(str(tenant_raw)) if tenant_raw else None
    roles = claims.get("roles") or claims.get("role") or ()
    if isinstance(roles, str):
        roles = [roles]
    return Principal(
        subject=str(claims.get("sub") or claims.get("email") or "oidc"),
        source="oidc",
        tenant_id=tenant,
        roles=parse_roles(roles) or parse_roles(("viewer",)),
        email=str(claims.get("email")) if claims.get("email") else None,
        attributes={
            key: value
            for key, value in claims.items()
            if key not in {"sub", "email", "roles", "role", "tenant_id", "tid"}
        },
    )
