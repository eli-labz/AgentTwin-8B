"""Process-wide enterprise service context.

Bundles the store, audit log, authentication chain, secret provider and
runtime settings so routers, CLI commands, workers and the Harbor adapter
share one composition root. Nothing in here is request-scoped.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from matraix.enterprise.audit import AuditLog
from matraix.enterprise.auth.providers import ChainAuthProvider, build_auth_provider_from_env
from matraix.enterprise.repositories import EnterpriseRepository
from matraix.enterprise.secrets import SecretProvider, default_secret_provider
from matraix.enterprise.store import open_enterprise_store

__all__ = ["EnterpriseContext", "EnterpriseSettings", "build_context"]

ARTIFACT_ROOT_ENV = "MATRIX_ENTERPRISE_ARTIFACT_ROOT"
DEFAULT_ARTIFACT_ROOT = ".enterprise/artifacts"


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "persona").is_dir():
            return candidate
    return Path.cwd()


@dataclass(frozen=True)
class EnterpriseSettings:
    repo_root: Path
    artifact_root: Path
    environment: str = "development"
    service_name: str = "agenttwin-enterprise"
    version: str = "v1"

    @classmethod
    def from_env(cls, repo_root: Path | None = None) -> "EnterpriseSettings":
        root = repo_root or _find_repo_root()
        artifact = Path(os.environ.get(ARTIFACT_ROOT_ENV) or (root / DEFAULT_ARTIFACT_ROOT))
        if not artifact.is_absolute():
            artifact = root / artifact
        return cls(
            repo_root=root,
            artifact_root=artifact,
            environment=os.environ.get("MATRIX_ENTERPRISE_ENV", "development"),
        )


@dataclass
class EnterpriseContext:
    store: EnterpriseRepository
    audit: AuditLog
    auth: ChainAuthProvider
    secrets: SecretProvider
    settings: EnterpriseSettings
    extras: dict[str, Any] = field(default_factory=dict)

    def tenant_ids(self) -> list[str]:
        return [tenant.id.value for tenant in self.store.list_tenants()]


def build_context(
    store: EnterpriseRepository | None = None,
    *,
    secrets: SecretProvider | None = None,
    settings: EnterpriseSettings | None = None,
    auth: ChainAuthProvider | None = None,
) -> EnterpriseContext:
    repository = store or open_enterprise_store()
    secret_provider = secrets or default_secret_provider()
    resolved_settings = settings or EnterpriseSettings.from_env()
    redact_values = tuple(
        value for value in (secret_provider.get(name) for name in secret_provider.names()) if value
    )
    audit = AuditLog(repository, redact_values=redact_values)  # type: ignore[arg-type]
    ctx = EnterpriseContext(
        store=repository,
        audit=audit,
        auth=auth or ChainAuthProvider(providers=[]),
        secrets=secret_provider,
        settings=resolved_settings,
    )
    if auth is None:
        ctx.auth = build_auth_provider_from_env(
            store=repository,  # type: ignore[arg-type]
            tenant_ids=ctx.tenant_ids,
            secrets=secret_provider,
        )
    return ctx
