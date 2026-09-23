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
        CREATE TABLE enterprise_records (
            tenant_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            id TEXT NOT NULL,
            organization_id TEXT,
            parent_id TEXT,
            status TEXT,
            version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            created_by TEXT,
            body TEXT NOT NULL,
            PRIMARY KEY (tenant_id, kind, id)
        );
        CREATE INDEX idx_records_tenant_kind_org ON enterprise_records(tenant_id, kind, organization_id);
        CREATE INDEX idx_records_tenant_kind_parent ON enterprise_records(tenant_id, kind, parent_id);
        CREATE INDEX idx_records_tenant_kind_status ON enterprise_records(tenant_id, kind, status);
        CREATE INDEX idx_records_tenant_kind_created ON enterprise_records(tenant_id, kind, created_at);

        CREATE TABLE audit_events (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            ts TEXT NOT NULL,
            actor TEXT NOT NULL,
            actor_kind TEXT NOT NULL,
            action TEXT NOT NULL,
            category TEXT NOT NULL,
            resource_type TEXT,
            resource_id TEXT,
            outcome TEXT NOT NULL,
            request_id TEXT,
            details_json TEXT NOT NULL,
            prev_hash TEXT,
            hash TEXT NOT NULL,
            PRIMARY KEY (tenant_id, id),
            UNIQUE (tenant_id, seq)
        );
        CREATE INDEX idx_audit_tenant_seq ON audit_events(tenant_id, seq);
        CREATE INDEX idx_audit_tenant_resource ON audit_events(tenant_id, resource_id);

        CREATE TABLE work_items (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            shard_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            status TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            lease_owner TEXT,
            lease_expires_at TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            idempotency_key TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            result_ref TEXT,
            error_class TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            heartbeat_at TEXT,
            PRIMARY KEY (tenant_id, id),
            UNIQUE (tenant_id, idempotency_key)
        );
        CREATE INDEX idx_work_items_lease ON work_items(tenant_id, status, priority, seq);
        CREATE INDEX idx_work_items_run ON work_items(tenant_id, run_id, status);

        CREATE TABLE persona_snapshots (
            tenant_id TEXT NOT NULL,
            population_version_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            persona_ref TEXT NOT NULL,
            path TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            segment TEXT,
            source TEXT,
            summary_json TEXT NOT NULL,
            PRIMARY KEY (tenant_id, population_version_id, seq)
        );

        CREATE TABLE idempotency_keys (
            tenant_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            key TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, scope, key)
        );
        """,
    ),
)



def _postgres_ddl(sqlite_sql: str) -> str:
    """Translate the portable subset of our SQLite DDL to PostgreSQL."""
    return (
        sqlite_sql.replace(" INTEGER NOT NULL DEFAULT", " BIGINT NOT NULL DEFAULT")
        .replace(" INTEGER NOT NULL", " BIGINT NOT NULL")
        .replace(" INTEGER,", " BIGINT,")
        .replace(" REAL NOT NULL", " DOUBLE PRECISION NOT NULL")
    )


# PostgreSQL applies the same versions. Versions 1-2 (legacy typed tables) are
# kept for parity so both dialects report identical schema versions; the
# PostgreSQL store persists those entities through ``enterprise_records``.
POSTGRES_SCHEMA_MIGRATIONS: tuple[tuple[int, str], ...] = tuple(
    (version, _postgres_ddl(sql)) for version, sql in SCHEMA_MIGRATIONS
)

LATEST_SCHEMA_VERSION = max(version for version, _ in SCHEMA_MIGRATIONS)


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


def _version_of(row) -> int:
    """Read a ``version`` column from a tuple row or a mapping row factory."""
    if isinstance(row, dict):
        return int(row["version"])
    try:
        return int(row[0])
    except (KeyError, TypeError):
        return int(row["version"])


def _split_statements(sql: str) -> list[str]:
    return [part.strip() for part in sql.split(";") if part.strip()]


def apply_postgres_migrations(
    connection,
    migrations: Iterable[tuple[int, str]] = POSTGRES_SCHEMA_MIGRATIONS,
) -> list[int]:
    """Apply pending migrations on a psycopg connection (one transaction each)."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version BIGINT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        cursor.execute("SELECT version FROM schema_migrations")
        applied = {int(_version_of(row)) for row in cursor.fetchall()}
    connection.commit()
    newly: list[int] = []
    for version, sql in migrations:
        if version in applied:
            continue
        with connection.cursor() as cursor:
            for statement in _split_statements(sql):
                cursor.execute(statement)
            cursor.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (%s, %s)",
                (version, _utcnow_iso()),
            )
        connection.commit()
        newly.append(version)
    return newly


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
