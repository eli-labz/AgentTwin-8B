"""Factory for enterprise repositories.

Default backend is in-memory so existing tests and local CLI stay unchanged.
Set ``MATRIX_ENTERPRISE_STORE=sqlite`` for a durable file (path from
``MATRIX_ENTERPRISE_DB``, default ``.enterprise/store.sqlite``) or
``MATRIX_ENTERPRISE_STORE=postgres`` with ``MATRIX_ENTERPRISE_DATABASE_URL``
for production.
"""

from __future__ import annotations

import os
from pathlib import Path

from matraix.enterprise.entities import Organization, Tenant
from matraix.enterprise.errors import EnterpriseSchemaError
from matraix.enterprise.ids import EntityKind, OrganizationId, TenantId, new_id
from matraix.enterprise.repositories import (
    EnterpriseRepository,
    InMemoryEnterpriseStore,
)
from matraix.enterprise.sqlite_store import SqliteEnterpriseStore

STORE_ENV = "MATRIX_ENTERPRISE_STORE"
DB_PATH_ENV = "MATRIX_ENTERPRISE_DB"
POSTGRES_DSN_ENV = "MATRIX_ENTERPRISE_DATABASE_URL"
DEFAULT_SQLITE_PATH = ".enterprise/store.sqlite"


def open_enterprise_store(
    *,
    backend: str | None = None,
    path: str | Path | None = None,
) -> EnterpriseRepository:
    """Return a store. Unknown backends fail closed."""
    resolved = (backend or os.environ.get(STORE_ENV) or "memory").strip().lower()
    if resolved in {"memory", "mem", "inmemory", "in-memory"}:
        return InMemoryEnterpriseStore()
    if resolved in {"sqlite", "sqlite3"}:
        db_path = path if path is not None else os.environ.get(DB_PATH_ENV)
        return SqliteEnterpriseStore(db_path or DEFAULT_SQLITE_PATH)
    if resolved in {"postgres", "postgresql", "pg"}:
        from matraix.enterprise.postgres_store import PostgresEnterpriseStore

        dsn = str(path) if path is not None else os.environ.get(POSTGRES_DSN_ENV)
        if not dsn:
            raise ValueError(
                f"{POSTGRES_DSN_ENV} must be set for the postgres enterprise store"
            )
        return PostgresEnterpriseStore(dsn)
    raise ValueError(
        f"unknown enterprise store {resolved!r}; use 'memory', 'sqlite' or 'postgres'"
    )


def default_organization_for(store: EnterpriseRepository, tenant_id: TenantId) -> Organization:
    organizations = store.list_organizations(tenant_id)
    if not organizations:
        raise EnterpriseSchemaError(
            f"tenant {tenant_id.value} has no organization; create one first"
        )
    return organizations[0]


def create_tenant_with_default_org(
    store: EnterpriseRepository,
    *,
    name: str,
    slug: str,
    tenant_id: TenantId | None = None,
) -> tuple[Tenant, Organization]:
    """Create a tenant and a same-named default organization."""
    resolved_id = tenant_id or TenantId(new_id(EntityKind.TENANT))
    tenant = Tenant(id=resolved_id, name=name, slug=slug)
    store.put_tenant(tenant)
    organization = Organization(
        id=OrganizationId(resolved_id, new_id(EntityKind.ORGANIZATION)),
        tenant_id=resolved_id,
        name=name,
    )
    store.put_organization(organization)
    return tenant, organization
