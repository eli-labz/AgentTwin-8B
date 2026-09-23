"""PostgreSQL-backed enterprise store (production persistence).

Requires ``psycopg`` (v3). Selected via ``MATRIX_ENTERPRISE_STORE=postgres`` and
``MATRIX_ENTERPRISE_DATABASE_URL``. Every entity — legacy dataclass entities and
Phase 1+ records — lives in ``enterprise_records`` partitioned by
``(tenant_id, kind, id)``; audit, work items, persona snapshots and idempotency
keys use their dedicated tables (see :mod:`matraix.enterprise.migrations`).

Tenant isolation, referential checks and optimistic concurrency follow the same
rules as the SQLite and in-memory stores.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from matraix.enterprise.entities import (
    Department,
    EnterprisePersona,
    Experiment,
    Organization,
    Population,
    Team,
    Tenant,
)
from matraix.enterprise.errors import EnterpriseSchemaError, EntityNotFoundError
from matraix.enterprise.graph import OrgEdge, OrgRelation, require_org_node
from matraix.enterprise.ids import (
    DepartmentId,
    ExperimentId,
    OrganizationId,
    OrgEdgeId,
    PersonaId,
    PopulationId,
    TeamId,
    TenantId,
)
from matraix.enterprise.legacy_codec import LEGACY_KINDS, decode_entity, encode_entity
from matraix.enterprise.migrations import apply_postgres_migrations
from matraix.enterprise.population_builder import PopulationDeclaration
from matraix.enterprise.records import SqlRecordStoreBase, _dumps, _iso
from matraix.enterprise.repositories import _require_tenant

__all__ = ["PostgresEnterpriseStore"]


def _connect(dsn: str) -> Any:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "psycopg is required for the postgres enterprise store: "
            "pip install 'matraix[postgres]'"
        ) from exc
    return psycopg.connect(dsn, row_factory=dict_row)


class PostgresEnterpriseStore(SqlRecordStoreBase):
    """Production store. One connection per instance; guarded by an RLock."""

    dialect = "postgres"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._lock = threading.RLock()
        self._tx_depth = 0
        self._conn = _connect(dsn)
        apply_postgres_migrations(self._conn)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ #
    # legacy entity helpers (JSON documents in enterprise_records)
    # ------------------------------------------------------------------ #
    def _legacy_put(self, tenant_value: str, kind: str, local_id: str, entity: Any, *, organization_id: str | None = None, parent_id: str | None = None) -> None:
        body = _dumps(encode_entity(entity))
        now = _iso(__import__("matraix.enterprise.domain.base", fromlist=["utcnow"]).utcnow())
        self._execute(
            """
            INSERT INTO enterprise_records(
                tenant_id, kind, id, organization_id, parent_id, status, version,
                created_at, updated_at, created_by, body
            ) VALUES (?, ?, ?, ?, ?, 'active', 1, ?, ?, NULL, ?)
            ON CONFLICT(tenant_id, kind, id) DO UPDATE SET
                organization_id = excluded.organization_id,
                parent_id = excluded.parent_id,
                updated_at = excluded.updated_at,
                version = enterprise_records.version + 1,
                body = excluded.body
            """,
            (tenant_value, kind, local_id, organization_id, parent_id, now, now, body),
        )
        self._commit()

    def _legacy_get(self, tenant_value: str, kind: str, local_id: str) -> Any | None:
        row = self._fetchone(
            "SELECT body FROM enterprise_records WHERE tenant_id = ? AND kind = ? AND id = ?",
            (tenant_value, kind, local_id),
        )
        if row is None:
            return None
        return decode_entity(kind, json.loads(self._row(row)["body"]))

    def _legacy_list(self, tenant_value: str, kind: str) -> list[Any]:
        rows = self._fetchall(
            "SELECT body FROM enterprise_records WHERE tenant_id = ? AND kind = ? ORDER BY created_at, id",
            (tenant_value, kind),
        )
        return [decode_entity(kind, json.loads(self._row(row)["body"])) for row in rows]

    def _require_known_tenant(self, tenant_id: TenantId) -> None:
        if self._legacy_get(tenant_id.value, LEGACY_KINDS[Tenant], tenant_id.value) is None:
            raise EntityNotFoundError(f"unknown tenant {tenant_id.value}")

    def _require_organization(self, tenant_id: TenantId, organization_id: OrganizationId) -> None:
        if self._legacy_get(tenant_id.value, LEGACY_KINDS[Organization], organization_id.value) is None:
            raise EntityNotFoundError(f"unknown organization {organization_id.value}")

    # ------------------------------------------------------------------ #
    # tenants
    # ------------------------------------------------------------------ #
    def put_tenant(self, tenant: Tenant) -> Tenant:
        with self._lock:
            for existing in self.list_tenants():
                if existing.slug == tenant.slug and existing.id != tenant.id:
                    raise EnterpriseSchemaError(f"tenant slug {tenant.slug!r} already exists")
            self._legacy_put(tenant.id.value, LEGACY_KINDS[Tenant], tenant.id.value, tenant)
            return tenant

    def get_tenant(self, tenant_id: TenantId) -> Tenant:
        with self._lock:
            item = self._legacy_get(tenant_id.value, LEGACY_KINDS[Tenant], tenant_id.value)
            if item is None:
                raise EntityNotFoundError(f"unknown tenant {tenant_id.value}")
            return item

    def list_tenants(self) -> list[Tenant]:
        with self._lock:
            rows = self._fetchall(
                "SELECT body FROM enterprise_records WHERE kind = ? ORDER BY created_at, id",
                (LEGACY_KINDS[Tenant],),
            )
            return [decode_entity(LEGACY_KINDS[Tenant], json.loads(self._row(row)["body"])) for row in rows]

    # ------------------------------------------------------------------ #
    # organizations / departments / teams
    # ------------------------------------------------------------------ #
    def put_organization(self, organization: Organization) -> Organization:
        with self._lock:
            self._require_known_tenant(organization.tenant_id)
            if organization.parent_id is not None:
                self._require_organization(organization.tenant_id, organization.parent_id)
            self._legacy_put(
                organization.tenant_id.value,
                LEGACY_KINDS[Organization],
                organization.id.value,
                organization,
                organization_id=organization.id.value,
                parent_id=organization.parent_id.value if organization.parent_id else None,
            )
            return organization

    def get_organization(self, tenant_id: TenantId, organization_id: OrganizationId) -> Organization:
        _require_tenant(tenant_id, organization_id)
        with self._lock:
            item = self._legacy_get(tenant_id.value, LEGACY_KINDS[Organization], organization_id.value)
            if item is None:
                raise EntityNotFoundError(f"unknown organization {organization_id.value}")
            return item

    def list_organizations(self, tenant_id: TenantId) -> list[Organization]:
        with self._lock:
            self._require_known_tenant(tenant_id)
            return self._legacy_list(tenant_id.value, LEGACY_KINDS[Organization])

    def put_department(self, department: Department) -> Department:
        with self._lock:
            self._require_known_tenant(department.tenant_id)
            self._require_organization(department.tenant_id, department.organization_id)
            self._legacy_put(
                department.tenant_id.value,
                LEGACY_KINDS[Department],
                department.id.value,
                department,
                organization_id=department.organization_id.value,
            )
            return department

    def get_department(self, tenant_id: TenantId, department_id: DepartmentId) -> Department:
        _require_tenant(tenant_id, department_id)
        with self._lock:
            item = self._legacy_get(tenant_id.value, LEGACY_KINDS[Department], department_id.value)
            if item is None:
                raise EntityNotFoundError(f"unknown department {department_id.value}")
            return item

    def put_team(self, team: Team) -> Team:
        with self._lock:
            self._require_known_tenant(team.tenant_id)
            self._require_organization(team.tenant_id, team.organization_id)
            if team.department_id is not None:
                self.get_department(team.tenant_id, team.department_id)
            self._legacy_put(
                team.tenant_id.value,
                LEGACY_KINDS[Team],
                team.id.value,
                team,
                organization_id=team.organization_id.value,
                parent_id=team.department_id.value if team.department_id else None,
            )
            return team

    def get_team(self, tenant_id: TenantId, team_id: TeamId) -> Team:
        _require_tenant(tenant_id, team_id)
        with self._lock:
            item = self._legacy_get(tenant_id.value, LEGACY_KINDS[Team], team_id.value)
            if item is None:
                raise EntityNotFoundError(f"unknown team {team_id.value}")
            return item

    # ------------------------------------------------------------------ #
    # populations / personas / experiments
    # ------------------------------------------------------------------ #
    def put_population(self, population: Population) -> Population:
        with self._lock:
            self._require_known_tenant(population.tenant_id)
            self._require_organization(population.tenant_id, population.organization_id)
            self._legacy_put(
                population.tenant_id.value,
                LEGACY_KINDS[Population],
                population.id.value,
                population,
                organization_id=population.organization_id.value,
            )
            return population

    def get_population(self, tenant_id: TenantId, population_id: PopulationId) -> Population:
        _require_tenant(tenant_id, population_id)
        with self._lock:
            item = self._legacy_get(tenant_id.value, LEGACY_KINDS[Population], population_id.value)
            if item is None:
                raise EntityNotFoundError(f"unknown population {population_id.value}")
            return item

    def list_populations(self, tenant_id: TenantId) -> list[Population]:
        with self._lock:
            self._require_known_tenant(tenant_id)
            return self._legacy_list(tenant_id.value, LEGACY_KINDS[Population])

    def put_persona(self, persona: EnterprisePersona) -> EnterprisePersona:
        with self._lock:
            self._require_known_tenant(persona.tenant_id)
            self._require_organization(persona.tenant_id, persona.organization_id)
            if persona.population_id is not None:
                self.get_population(persona.tenant_id, persona.population_id)
            self._legacy_put(
                persona.tenant_id.value,
                LEGACY_KINDS[EnterprisePersona],
                persona.id.value,
                persona,
                organization_id=persona.organization_id.value,
                parent_id=persona.population_id.value if persona.population_id else None,
            )
            return persona

    def get_persona(self, tenant_id: TenantId, persona_id: PersonaId) -> EnterprisePersona:
        _require_tenant(tenant_id, persona_id)
        with self._lock:
            item = self._legacy_get(tenant_id.value, LEGACY_KINDS[EnterprisePersona], persona_id.value)
            if item is None:
                raise EntityNotFoundError(f"unknown persona {persona_id.value}")
            return item

    def list_personas(self, tenant_id: TenantId) -> list[EnterprisePersona]:
        with self._lock:
            self._require_known_tenant(tenant_id)
            return self._legacy_list(tenant_id.value, LEGACY_KINDS[EnterprisePersona])

    def put_experiment(self, experiment: Experiment) -> Experiment:
        with self._lock:
            self._require_known_tenant(experiment.tenant_id)
            self._require_organization(experiment.tenant_id, experiment.organization_id)
            for population_id in experiment.population_ids:
                self.get_population(experiment.tenant_id, population_id)
            self._legacy_put(
                experiment.tenant_id.value,
                LEGACY_KINDS[Experiment],
                experiment.id.value,
                experiment,
                organization_id=experiment.organization_id.value,
            )
            return experiment

    def get_experiment(self, tenant_id: TenantId, experiment_id: ExperimentId) -> Experiment:
        _require_tenant(tenant_id, experiment_id)
        with self._lock:
            item = self._legacy_get(tenant_id.value, LEGACY_KINDS[Experiment], experiment_id.value)
            if item is None:
                raise EntityNotFoundError(f"unknown experiment {experiment_id.value}")
            return item

    # ------------------------------------------------------------------ #
    # org edges / declarations
    # ------------------------------------------------------------------ #
    def put_org_edge(self, edge: OrgEdge) -> OrgEdge:
        with self._lock:
            self._require_known_tenant(edge.tenant_id)
            self._require_organization(edge.tenant_id, edge.organization_id)
            require_org_node(self, edge.tenant_id, edge.source_kind, edge.source_id)
            require_org_node(self, edge.tenant_id, edge.target_kind, edge.target_id)
            for existing in self._legacy_list(edge.tenant_id.value, LEGACY_KINDS[OrgEdge]):
                if existing.id != edge.id and existing.identity_key() == edge.identity_key():
                    raise EnterpriseSchemaError("duplicate org edge")
            self._legacy_put(
                edge.tenant_id.value,
                LEGACY_KINDS[OrgEdge],
                edge.id.value,
                edge,
                organization_id=edge.organization_id.value,
            )
            return edge

    def get_org_edge(self, tenant_id: TenantId, edge_id: OrgEdgeId) -> OrgEdge:
        _require_tenant(tenant_id, edge_id)
        with self._lock:
            item = self._legacy_get(tenant_id.value, LEGACY_KINDS[OrgEdge], edge_id.value)
            if item is None:
                raise EntityNotFoundError(f"unknown org edge {edge_id.value}")
            return item

    def list_org_edges(
        self,
        tenant_id: TenantId,
        *,
        organization_id: OrganizationId | None = None,
        relation: OrgRelation | None = None,
    ) -> list[OrgEdge]:
        with self._lock:
            self._require_known_tenant(tenant_id)
            items = self._legacy_list(tenant_id.value, LEGACY_KINDS[OrgEdge])
            if organization_id is not None:
                _require_tenant(tenant_id, organization_id)
                items = [item for item in items if item.organization_id == organization_id]
            if relation is not None:
                items = [item for item in items if item.relation is relation]
            return items

    def delete_org_edge(self, tenant_id: TenantId, edge_id: OrgEdgeId) -> None:
        with self._lock:
            self.get_org_edge(tenant_id, edge_id)
            self._execute(
                "DELETE FROM enterprise_records WHERE tenant_id = ? AND kind = ? AND id = ?",
                (tenant_id.value, LEGACY_KINDS[OrgEdge], edge_id.value),
            )
            self._commit()

    def put_population_declaration(self, declaration: PopulationDeclaration) -> PopulationDeclaration:
        with self._lock:
            self._require_known_tenant(declaration.tenant_id)
            self._require_organization(declaration.tenant_id, declaration.organization_id)
            self.get_population(declaration.tenant_id, declaration.population_id)
            self._legacy_put(
                declaration.tenant_id.value,
                LEGACY_KINDS[PopulationDeclaration],
                declaration.population_id.value,
                declaration,
                organization_id=declaration.organization_id.value,
                parent_id=declaration.population_id.value,
            )
            return declaration

    def get_population_declaration(self, tenant_id: TenantId, population_id: PopulationId) -> PopulationDeclaration:
        _require_tenant(tenant_id, population_id)
        with self._lock:
            item = self._legacy_get(
                tenant_id.value, LEGACY_KINDS[PopulationDeclaration], population_id.value
            )
            if item is None:
                raise EntityNotFoundError(f"unknown population declaration {population_id.value}")
            return item
