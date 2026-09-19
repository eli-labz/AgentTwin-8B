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
    (
        5,
        """
        CREATE TABLE executions (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            experiment_id TEXT NOT NULL,
            worker_kind TEXT NOT NULL,
            status TEXT NOT NULL,
            decision TEXT NOT NULL,
            plane TEXT NOT NULL,
            reasons_json TEXT NOT NULL,
            harbor_job_name TEXT,
            trial_slots INTEGER NOT NULL,
            concurrency INTEGER NOT NULL,
            artifact_ids_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id),
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        );

        CREATE INDEX idx_executions_tenant ON executions(tenant_id);

        CREATE TABLE artifacts (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            content_json TEXT NOT NULL,
            execution_id TEXT,
            experiment_id TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id),
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        );

        CREATE INDEX idx_artifacts_tenant ON artifacts(tenant_id);
        CREATE INDEX idx_artifacts_execution ON artifacts(tenant_id, execution_id);

        CREATE TABLE enterprise_events (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            kind TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            experiment_id TEXT,
            execution_id TEXT,
            tick INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id),
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        );

        CREATE INDEX idx_enterprise_events_tenant ON enterprise_events(tenant_id);
        CREATE INDEX idx_enterprise_events_execution
            ON enterprise_events(tenant_id, execution_id);
        """,
    ),
    (
        6,
        """
        CREATE TABLE enterprise_users (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            username TEXT NOT NULL,
            email TEXT,
            external_id TEXT,
            roles_json TEXT NOT NULL,
            active INTEGER NOT NULL,
            attributes_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id),
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        );

        CREATE UNIQUE INDEX idx_users_username
            ON enterprise_users(tenant_id, username);
        CREATE INDEX idx_users_external
            ON enterprise_users(tenant_id, external_id);

        CREATE TABLE audit_events (
            id TEXT PRIMARY KEY,
            tenant_id TEXT,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            resource TEXT NOT NULL,
            result TEXT NOT NULL,
            created_at TEXT NOT NULL,
            policy TEXT,
            trace_id TEXT,
            ip TEXT,
            details_json TEXT NOT NULL
        );

        CREATE INDEX idx_audit_tenant ON audit_events(tenant_id, created_at);

        CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events
        BEGIN
            SELECT RAISE(ABORT, 'audit log is append-only');
        END;

        CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events
        BEGIN
            SELECT RAISE(ABORT, 'audit log is append-only');
        END;

        CREATE TABLE governance_reviews (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            title TEXT NOT NULL,
            notes TEXT NOT NULL,
            reviewer TEXT,
            sign_off INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            details_json TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id),
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        );

        CREATE INDEX idx_governance_tenant ON governance_reviews(tenant_id);
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
