"""Secret provider interface and log redaction.

Enterprise code never reads credentials directly from ``os.environ`` at the
point of use; it asks a :class:`SecretProvider`. Today that is environment
variables (:class:`EnvSecretProvider`) or an in-process map for tests
(:class:`StaticSecretProvider`). External managers plug in behind the same
protocol later without touching call sites.

:func:`redact` and :class:`RedactingFilter` keep secret values out of logs,
traces and artifacts. Provider API keys recognised by
``matraix.provider_credentials`` are redacted automatically.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Protocol

__all__ = [
    "EnvSecretProvider",
    "RedactingFilter",
    "SecretProvider",
    "StaticSecretProvider",
    "default_secret_provider",
    "redact",
    "sensitive_env_names",
]

_SENSITIVE_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_PRIVATE_KEY", "_DSN", "_DATABASE_URL")
_SENSITIVE_EXACT = frozenset({"MATRIX_ENTERPRISE_AUTH_TOKENS", "REMOTE_RUNNER_API_KEY"})
_TOKEN_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"atsk_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_\-\.=]{8,}"),
    re.compile(r"(?i)(postgres(?:ql)?://[^:/\s]+:)[^@\s]+@"),
)


class SecretProvider(Protocol):
    def get(self, name: str) -> str | None: ...

    def names(self) -> Iterable[str]: ...


def sensitive_env_names(environ: Mapping[str, str] | None = None) -> list[str]:
    env = os.environ if environ is None else environ
    return sorted(
        name
        for name in env
        if name in _SENSITIVE_EXACT or any(name.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)
    )


@dataclass
class EnvSecretProvider:
    prefix: str = ""

    def get(self, name: str) -> str | None:
        value = os.environ.get(self.prefix + name)
        if value is None:
            return None
        value = value.strip()
        return value or None

    def names(self) -> Iterable[str]:
        return sensitive_env_names()


@dataclass
class StaticSecretProvider:
    values: dict[str, str] = field(default_factory=dict)

    def get(self, name: str) -> str | None:
        value = self.values.get(name)
        return value.strip() if value and value.strip() else None

    def names(self) -> Iterable[str]:
        return list(self.values)


def default_secret_provider() -> SecretProvider:
    return EnvSecretProvider()


def redact(text: str, secrets: Iterable[str] = (), *, replacement: str = "[REDACTED]") -> str:
    """Replace known secret values and common credential shapes in ``text``."""
    out = str(text)
    for value in sorted({item for item in secrets if item and len(item) >= 6}, key=len, reverse=True):
        out = out.replace(value, replacement)
    for pattern in _TOKEN_PATTERNS:
        if pattern.groups:
            out = pattern.sub(lambda match: match.group(1) + replacement + ("@" if match.group(0).endswith("@") else ""), out)
        else:
            out = pattern.sub(replacement, out)
    return out


class RedactingFilter(logging.Filter):
    """Logging filter that redacts configured secrets from every record."""

    def __init__(self, provider: SecretProvider | None = None) -> None:
        super().__init__()
        self._provider = provider or default_secret_provider()

    def _values(self) -> list[str]:
        values: list[str] = []
        for name in self._provider.names():
            value = self._provider.get(name)
            if value:
                values.append(value)
        return values

    def filter(self, record: logging.LogRecord) -> bool:
        values = self._values()
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never break logging
            return True
        record.msg = redact(message, values)
        record.args = ()
        return True
