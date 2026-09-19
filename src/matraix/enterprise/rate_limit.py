"""In-process rate limiter for sensitive enterprise routes.

Disabled unless ``MATRIX_ENTERPRISE_RATE_LIMIT`` is set or the process is
production. Tests opt in via the environment. No external service.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque

from matraix.enterprise.http_security import is_production

RATE_LIMIT_ENV = "MATRIX_ENTERPRISE_RATE_LIMIT"
RATE_WINDOW_ENV = "MATRIX_ENTERPRISE_RATE_WINDOW_SECONDS"
SENSITIVE_LIMIT_ENV = "MATRIX_ENTERPRISE_SENSITIVE_RATE_LIMIT"

_DEFAULT_PROD_LIMIT = 120
_DEFAULT_SENSITIVE = 30
_DEFAULT_WINDOW = 60.0


def rate_limit_enabled() -> bool:
    raw = os.environ.get(RATE_LIMIT_ENV, "").strip()
    if raw in {"0", "off", "false", "no"}:
        return False
    if raw:
        return True
    return is_production()


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def default_limit() -> int:
    return _int_env(RATE_LIMIT_ENV, _DEFAULT_PROD_LIMIT if is_production() else 600)


def sensitive_limit() -> int:
    return _int_env(SENSITIVE_LIMIT_ENV, _DEFAULT_SENSITIVE if is_production() else 120)


def window_seconds() -> float:
    raw = os.environ.get(RATE_WINDOW_ENV, "").strip()
    if not raw:
        return _DEFAULT_WINDOW
    try:
        return max(1.0, float(raw))
    except ValueError:
        return _DEFAULT_WINDOW


def is_sensitive_path(path: str) -> bool:
    cleaned = (path or "/").rstrip("/") or "/"
    return any(
        cleaned.startswith(prefix)
        for prefix in (
            "/api/v1/models/complete",
            "/api/v1/experiments",
            "/api/v1/executions",
            "/api/v1/auth/dev-token",
            "/api/v1/auth/session",
            "/api/v1/scim",
            "/api/v1/tenants",
        )
    ) and cleaned != "/api/v1/experiments"


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, *, limit: int, window: float) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            cutoff = now - window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True


_LIMITER = RateLimiter()


def check_rate_limit(key: str, *, sensitive: bool = False) -> bool:
    if not rate_limit_enabled():
        return True
    limit = sensitive_limit() if sensitive else default_limit()
    return _LIMITER.allow(key, limit=limit, window=window_seconds())
