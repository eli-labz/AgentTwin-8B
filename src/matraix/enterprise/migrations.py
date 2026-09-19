"""SQLite schema migrations for the enterprise domain store.

SQL is applied in version order. Business rules stay on domain entities —
these statements only create tables and indexes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

SCHEMA_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE tenants (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );

        CREATE TABLE organizations (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            name TEXT NOT NULL,
            parent_id TEXT,
            industry TEXT,
            geography TEXT,
            PRIMARY KEY (tenant_id, id),
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        );

        CREATE TABLE departments (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            name TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id),
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        );

        CREATE TABLE teams (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            department_id TEXT,
            name TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id),
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        );

        CREATE TABLE populations (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            target_size INTEGER,
            team_id TEXT,
            PRIMARY KEY (tenant_id, id),
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        );

        CREATE TABLE personas (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            legacy_persona_id TEXT NOT NULL,
            version TEXT NOT NULL,
            source TEXT NOT NULL,
            dimensions_json TEXT NOT NULL,
            display_name TEXT,
            provenance_json TEXT,
            population_id TEXT,
            enterprise_json TEXT,
            data_classification TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id),
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        );

        CREATE TABLE experiments (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            hypothesis TEXT NOT NULL,
            objective TEXT NOT NULL,
            population_ids_json TEXT NOT NULL,
            random_seed INTEGER,
            data_classification TEXT NOT NULL,
            default_policy TEXT NOT NULL,
            execution_budget_json TEXT NOT NULL,
            variables_json TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id),
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        );

        CREATE INDEX idx_organizations_tenant ON organizations(tenant_id);
        CREATE INDEX idx_populations_tenant ON populations(tenant_id);
        CREATE INDEX idx_personas_tenant ON personas(tenant_id);
        CREATE INDEX idx_personas_population ON personas(tenant_id, population_id);
        CREATE INDEX idx_experiments_tenant ON experiments(tenant_id);
        """,
    ),
    (
        2,
        """
        CREATE TABLE org_edges (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_id TEXT NOT NULL,
            target_kind TEXT NOT NULL,
            target_id TEXT NOT NULL,
            attributes_json TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id),
            FOREIGN KEY (tenant_id) REFERENCES tenants(id),
            UNIQUE (
                tenant_id, relation, source_kind, source_id, target_kind, target_id
            )
        );

        CREATE INDEX idx_org_edges_tenant ON org_edges(tenant_id);
        CREATE INDEX idx_org_edges_org ON org_edges(tenant_id, organization_id);

        CREATE TABLE population_declarations (
            tenant_id TEXT NOT NULL,
            population_id TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            target_size INTEGER NOT NULL,
            backend TEXT NOT NULL,
            segments_json TEXT NOT NULL,
            constraints_json TEXT NOT NULL,
            include_org_structure INTEGER NOT NULL,
            privacy_mode TEXT NOT NULL,
            resolved_counts_json TEXT NOT NULL,
            PRIMARY KEY (tenant_id, population_id),
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        );

        CREATE INDEX idx_population_declarations_tenant
            ON population_declarations(tenant_id);
        """,
    ),
    (
        3,
        """
        ALTER TABLE experiments ADD COLUMN launch_json TEXT;
        """,
    ),
    (
        4,
        """
        CREATE TABLE tenant_model_policies (
            tenant_id TEXT PRIMARY KEY,
            allowed_providers_json TEXT NOT NULL,
            denied_providers_json TEXT NOT NULL,
            required_residency TEXT,
            max_cost_score REAL,
            max_latency_ms INTEGER,
            allowed_capabilities_json TEXT NOT NULL,
            allow_external INTEGER NOT NULL,
            allow_live INTEGER NOT NULL,
            default_decision TEXT NOT NULL,
            denied_actions_json TEXT NOT NULL,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        );
        """,
    ),
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def applied_versions(connection) -> set[int]:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    rows = connection.execute("SELECT version FROM schema_migrations")
    return {int(row[0]) for row in rows}


def apply_migrations(
    connection,
    migrations: Iterable[tuple[int, str]] = SCHEMA_MIGRATIONS,
) -> list[int]:
    """Apply pending migrations. Returns newly applied version numbers."""
    applied = applied_versions(connection)
    newly: list[int] = []
    for version, sql in migrations:
        if version in applied:
            continue
        connection.executescript(sql)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (version, _utcnow_iso()),
        )
        newly.append(version)
    connection.commit()
    return newly
