"""Auth and CORS helpers for the enterprise API.

Dev keeps an explicit Vite / Playground allow-list. Production
(``MATRIX_ENTERPRISE_ENV=production``) closes CORS unless
``MATRIX_ENTERPRISE_CORS_ORIGINS`` is set. Tokens stay in the environment.
"""

from __future__ import annotations

import os

API_TOKEN_ENV = "MATRIX_ENTERPRISE_API_TOKEN"
REQUIRE_AUTH_ENV = "MATRIX_ENTERPRISE_REQUIRE_AUTH"
ENV_NAME = "MATRIX_ENTERPRISE_ENV"
CORS_ORIGINS_ENV = "MATRIX_ENTERPRISE_CORS_ORIGINS"

DEV_CORS_ORIGINS: tuple[str, ...] = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8765",
    "http://127.0.0.1:8765",
    "http://localhost:8090",
    "http://127.0.0.1:8090",
)

PUBLIC_PATHS = frozenset(
    {
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/console",
        "/console/",
        "/api/v1/auth/oidc",
        "/api/v1/auth/csrf",
    }
)

SESSION_COOKIE = "matrix_enterprise_session"
CSRF_COOKIE = "matrix_enterprise_csrf"
CSRF_HEADER = "X-CSRF-Token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def enterprise_env() -> str:
    raw = os.environ.get(ENV_NAME, "dev").strip().lower()
    return raw or "dev"


def is_production() -> bool:
    return enterprise_env() in {"prod", "production"}


def configured_token() -> str | None:
    token = os.environ.get(API_TOKEN_ENV, "").strip()
    return token or None


def auth_is_required() -> bool:
    flag = os.environ.get(REQUIRE_AUTH_ENV, "").strip().lower()
    if flag in {"1", "true", "yes"}:
        return True
    if is_production():
        return True
    return configured_token() is not None


def resolve_cors_origins() -> list[str]:
    raw = os.environ.get(CORS_ORIGINS_ENV, "").strip()
    if raw:
        return [item.strip() for item in raw.split(",") if item.strip()]
    if is_production():
        return []
    return list(DEV_CORS_ORIGINS)


def is_public_path(path: str) -> bool:
    cleaned = (path or "/").rstrip("/") or "/"
    if cleaned in PUBLIC_PATHS or path in PUBLIC_PATHS:
        return True
    return cleaned.startswith("/console/")


def cookie_secure_defaults() -> dict[str, object]:
    """HttpOnly + SameSite=Lax; Secure in production."""
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": is_production(),
        "path": "/",
    }


def csrf_cookie_defaults() -> dict[str, object]:
    defaults = cookie_secure_defaults()
    defaults["httponly"] = False
    return defaults


def new_csrf_token() -> str:
    import secrets

    return secrets.token_urlsafe(32)


def decode_session_value(raw: str | None) -> dict[str, str]:
    """Parse ``subject:tenant`` session cookie. Not a signed JWT."""
    if not raw:
        return {}
    parts = str(raw).split(":", 2)
    if len(parts) < 2:
        return {"subject": str(raw)}
    return {"subject": parts[0], "tenant_id": parts[1], "roles": parts[2] if len(parts) > 2 else ""}


def encode_session_value(*, subject: str, tenant_id: str = "", roles: str = "") -> str:
    return f"{subject}:{tenant_id}:{roles}"


def csrf_is_required(method: str, *, has_session_cookie: bool) -> bool:
    return has_session_cookie and (method or "").upper() in UNSAFE_METHODS
