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
    }
)


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
