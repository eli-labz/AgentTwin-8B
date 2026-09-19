"""SQLite-backed enterprise repository.

Reconstructs domain entities from rows. Referential and tenant checks stay on
the entity constructors and the same put/get rules as the in-memory store.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from matraix.enterprise.entities import (
    Department,
    EnterprisePersona,
    ExecutionBudget,
    Experiment,
    Organization,
    Population,
    Team,
    Tenant,
)
from matraix.enterprise.experiment_launch import apply_launch_payload
from matraix.enterprise.errors import (
    CrossTenantAccessError,
    EnterpriseSchemaError,
    EntityNotFoundError,
)
from matraix.enterprise.graph import (
    OrgEdge,
    OrgNodeKind,
    OrgRelation,
    require_org_node,
)
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
from matraix.enterprise.population_builder import (
    GenerationBackend,
    PopulationDeclaration,
    PopulationSegment,
)
from matraix.enterprise.migrations import apply_migrations
from matraix.enterprise.model_gateway import ModelPolicy, default_model_policy
from matraix.enterprise.persona_schema import parse_enterprise_block
from matraix.enterprise.policy import DataClassification, PolicyDecision
from matraix.enterprise.repositories import _require_tenant


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


class SqliteEnterpriseStore:
    """File or ``:memory:`` store selected by ``MATRIX_ENTERPRISE_STORE=sqlite``."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        apply_migrations(self._conn)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _require_known_tenant(self, tenant_id: TenantId) -> None:
        row = self._conn.execute(
            "SELECT 1 FROM tenants WHERE id = ?",
            (tenant_id.value,),
        ).fetchone()
        if row is None:
            raise EntityNotFoundError(f"unknown tenant {tenant_id.value}")

    def put_tenant(self, tenant: Tenant) -> Tenant:
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO tenants(id, name, slug, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        slug = excluded.slug
                    """,
                    (
                        tenant.id.value,
                        tenant.name,
                        tenant.slug,
                        tenant.created_at.isoformat(),
                    ),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                raise EnterpriseSchemaError(
                    f"tenant slug {tenant.slug!r} already exists"
                ) from exc
            return tenant

    def get_tenant(self, tenant_id: TenantId) -> Tenant:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, slug, created_at FROM tenants WHERE id = ?",
                (tenant_id.value,),
            ).fetchone()
            if row is None:
                raise EntityNotFoundError(f"unknown tenant {tenant_id.value}")
            return Tenant(
                id=TenantId(row["id"]),
                name=row["name"],
                slug=row["slug"],
                created_at=_parse_dt(row["created_at"]),
            )

    def list_tenants(self) -> list[Tenant]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, slug, created_at FROM tenants ORDER BY slug"
            ).fetchall()
            return [
                Tenant(
                    id=TenantId(row["id"]),
                    name=row["name"],
                    slug=row["slug"],
                    created_at=_parse_dt(row["created_at"]),
                )
                for row in rows
            ]

    def put_organization(self, organization: Organization) -> Organization:
        with self._lock:
            self._require_known_tenant(organization.tenant_id)
            self._conn.execute(
                """
                INSERT INTO organizations(
                    tenant_id, id, name, parent_id, industry, geography
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, id) DO UPDATE SET
                    name = excluded.name,
                    parent_id = excluded.parent_id,
                    industry = excluded.industry,
                    geography = excluded.geography
                """,
                (
                    organization.tenant_id.value,
                    organization.id.value,
                    organization.name,
                    organization.parent_id.value if organization.parent_id else None,
                    organization.industry,
                    organization.geography,
                ),
            )
            self._conn.commit()
            return organization

    def get_organization(
        self, tenant_id: TenantId, organization_id: OrganizationId
    ) -> Organization:
        _require_tenant(tenant_id, organization_id)
        with self._lock:
            self._require_known_tenant(tenant_id)
            row = self._conn.execute(
                """
                SELECT tenant_id, id, name, parent_id, industry, geography
                FROM organizations WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id.value, organization_id.value),
            ).fetchone()
            if row is None:
                raise EntityNotFoundError(
                    f"unknown organization {organization_id.value}"
                )
            return self._organization_from_row(row)

    def list_organizations(self, tenant_id: TenantId) -> list[Organization]:
        with self._lock:
            self._require_known_tenant(tenant_id)
            rows = self._conn.execute(
                """
                SELECT tenant_id, id, name, parent_id, industry, geography
                FROM organizations WHERE tenant_id = ? ORDER BY id
                """,
                (tenant_id.value,),
            ).fetchall()
            return [self._organization_from_row(row) for row in rows]

    def _organization_from_row(self, row: sqlite3.Row) -> Organization:
        tenant = TenantId(row["tenant_id"])
        parent = (
            OrganizationId(tenant, row["parent_id"]) if row["parent_id"] else None
        )
        return Organization(
            id=OrganizationId(tenant, row["id"]),
            tenant_id=tenant,
            name=row["name"],
            parent_id=parent,
            industry=row["industry"],
            geography=row["geography"],
        )

    def put_department(self, department: Department) -> Department:
        with self._lock:
            self._require_known_tenant(department.tenant_id)
            self._require_organization(
                department.tenant_id, department.organization_id
            )
            self._conn.execute(
                """
                INSERT INTO departments(tenant_id, id, organization_id, name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(tenant_id, id) DO UPDATE SET
                    organization_id = excluded.organization_id,
                    name = excluded.name
                """,
                (
                    department.tenant_id.value,
                    department.id.value,
                    department.organization_id.value,
                    department.name,
                ),
            )
            self._conn.commit()
            return department

    def get_department(
        self, tenant_id: TenantId, department_id: DepartmentId
    ) -> Department:
        _require_tenant(tenant_id, department_id)
        with self._lock:
            self._require_known_tenant(tenant_id)
            row = self._conn.execute(
                """
                SELECT tenant_id, id, organization_id, name
                FROM departments WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id.value, department_id.value),
            ).fetchone()
            if row is None:
                raise EntityNotFoundError(f"unknown department {department_id.value}")
            tenant = TenantId(row["tenant_id"])
            return Department(
                id=DepartmentId(tenant, row["id"]),
                tenant_id=tenant,
                organization_id=OrganizationId(tenant, row["organization_id"]),
                name=row["name"],
            )

    def put_team(self, team: Team) -> Team:
        with self._lock:
            self._require_known_tenant(team.tenant_id)
            self._require_organization(team.tenant_id, team.organization_id)
            if team.department_id is not None:
                self.get_department(team.tenant_id, team.department_id)
            self._conn.execute(
                """
                INSERT INTO teams(
                    tenant_id, id, organization_id, department_id, name
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, id) DO UPDATE SET
                    organization_id = excluded.organization_id,
                    department_id = excluded.department_id,
                    name = excluded.name
                """,
                (
                    team.tenant_id.value,
                    team.id.value,
                    team.organization_id.value,
                    team.department_id.value if team.department_id else None,
                    team.name,
                ),
            )
            self._conn.commit()
            return team

    def get_team(self, tenant_id: TenantId, team_id: TeamId) -> Team:
        _require_tenant(tenant_id, team_id)
        with self._lock:
            self._require_known_tenant(tenant_id)
            row = self._conn.execute(
                """
                SELECT tenant_id, id, organization_id, department_id, name
                FROM teams WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id.value, team_id.value),
            ).fetchone()
            if row is None:
                raise EntityNotFoundError(f"unknown team {team_id.value}")
            tenant = TenantId(row["tenant_id"])
            department = (
                DepartmentId(tenant, row["department_id"])
                if row["department_id"]
                else None
            )
            return Team(
                id=TeamId(tenant, row["id"]),
                tenant_id=tenant,
                organization_id=OrganizationId(tenant, row["organization_id"]),
                department_id=department,
                name=row["name"],
            )

    def put_population(self, population: Population) -> Population:
        with self._lock:
            self._require_known_tenant(population.tenant_id)
            self._require_organization(population.tenant_id, population.organization_id)
            if population.team_id is not None:
                self.get_team(population.tenant_id, population.team_id)
            self._conn.execute(
                """
                INSERT INTO populations(
                    tenant_id, id, organization_id, name, description,
                    target_size, team_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, id) DO UPDATE SET
                    organization_id = excluded.organization_id,
                    name = excluded.name,
                    description = excluded.description,
                    target_size = excluded.target_size,
                    team_id = excluded.team_id
                """,
                (
                    population.tenant_id.value,
                    population.id.value,
                    population.organization_id.value,
                    population.name,
                    population.description,
                    population.target_size,
                    population.team_id.value if population.team_id else None,
                ),
            )
            self._conn.commit()
            return population

    def get_population(
        self, tenant_id: TenantId, population_id: PopulationId
    ) -> Population:
        _require_tenant(tenant_id, population_id)
        with self._lock:
            self._require_known_tenant(tenant_id)
            row = self._conn.execute(
                """
                SELECT tenant_id, id, organization_id, name, description,
                       target_size, team_id
                FROM populations WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id.value, population_id.value),
            ).fetchone()
            if row is None:
                raise EntityNotFoundError(f"unknown population {population_id.value}")
            return self._population_from_row(row)

    def list_populations(self, tenant_id: TenantId) -> list[Population]:
        with self._lock:
            self._require_known_tenant(tenant_id)
            rows = self._conn.execute(
                """
                SELECT tenant_id, id, organization_id, name, description,
                       target_size, team_id
                FROM populations WHERE tenant_id = ? ORDER BY id
                """,
                (tenant_id.value,),
            ).fetchall()
            return [self._population_from_row(row) for row in rows]

    def _population_from_row(self, row: sqlite3.Row) -> Population:
        tenant = TenantId(row["tenant_id"])
        team = TeamId(tenant, row["team_id"]) if row["team_id"] else None
        return Population(
            id=PopulationId(tenant, row["id"]),
            tenant_id=tenant,
            organization_id=OrganizationId(tenant, row["organization_id"]),
            name=row["name"],
            description=row["description"],
            target_size=row["target_size"],
            team_id=team,
        )

    def put_persona(self, persona: EnterprisePersona) -> EnterprisePersona:
        with self._lock:
            self._require_known_tenant(persona.tenant_id)
            self._require_organization(persona.tenant_id, persona.organization_id)
            if persona.population_id is not None:
                self.get_population(persona.tenant_id, persona.population_id)
            enterprise_json = (
                _json_dumps(persona.enterprise.to_dict())
                if persona.enterprise is not None
                else None
            )
            provenance_json = (
                _json_dumps(persona.provenance) if persona.provenance else None
            )
            self._conn.execute(
                """
                INSERT INTO personas(
                    tenant_id, id, organization_id, legacy_persona_id, version,
                    source, dimensions_json, display_name, provenance_json,
                    population_id, enterprise_json, data_classification
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, id) DO UPDATE SET
                    organization_id = excluded.organization_id,
                    legacy_persona_id = excluded.legacy_persona_id,
                    version = excluded.version,
                    source = excluded.source,
                    dimensions_json = excluded.dimensions_json,
                    display_name = excluded.display_name,
                    provenance_json = excluded.provenance_json,
                    population_id = excluded.population_id,
                    enterprise_json = excluded.enterprise_json,
                    data_classification = excluded.data_classification
                """,
                (
                    persona.tenant_id.value,
                    persona.id.value,
                    persona.organization_id.value,
                    persona.legacy_persona_id,
                    persona.version,
                    persona.source,
                    _json_dumps(persona.dimensions),
                    persona.display_name,
                    provenance_json,
                    persona.population_id.value if persona.population_id else None,
                    enterprise_json,
                    persona.data_classification.value,
                ),
            )
            self._conn.commit()
            return persona

    def get_persona(
        self, tenant_id: TenantId, persona_id: PersonaId
    ) -> EnterprisePersona:
        _require_tenant(tenant_id, persona_id)
        with self._lock:
            self._require_known_tenant(tenant_id)
            row = self._conn.execute(
                "SELECT * FROM personas WHERE tenant_id = ? AND id = ?",
                (tenant_id.value, persona_id.value),
            ).fetchone()
            if row is None:
                raise EntityNotFoundError(f"unknown persona {persona_id.value}")
            return self._persona_from_row(row)

    def list_personas(self, tenant_id: TenantId) -> list[EnterprisePersona]:
        with self._lock:
            self._require_known_tenant(tenant_id)
            rows = self._conn.execute(
                "SELECT * FROM personas WHERE tenant_id = ? ORDER BY id",
                (tenant_id.value,),
            ).fetchall()
            return [self._persona_from_row(row) for row in rows]

    def _persona_from_row(self, row: sqlite3.Row) -> EnterprisePersona:
        tenant = TenantId(row["tenant_id"])
        population = (
            PopulationId(tenant, row["population_id"])
            if row["population_id"]
            else None
        )
        provenance = (
            json.loads(row["provenance_json"]) if row["provenance_json"] else None
        )
        enterprise = (
            parse_enterprise_block(json.loads(row["enterprise_json"]))
            if row["enterprise_json"]
            else None
        )
        return EnterprisePersona(
            id=PersonaId(tenant, row["id"]),
            tenant_id=tenant,
            organization_id=OrganizationId(tenant, row["organization_id"]),
            legacy_persona_id=row["legacy_persona_id"],
            version=row["version"],
            source=row["source"],
            dimensions=json.loads(row["dimensions_json"]),
            display_name=row["display_name"],
            provenance=provenance,
            population_id=population,
            enterprise=enterprise,
            data_classification=DataClassification(row["data_classification"]),
        )

    def put_experiment(self, experiment: Experiment) -> Experiment:
        with self._lock:
            self._require_known_tenant(experiment.tenant_id)
            self._require_organization(experiment.tenant_id, experiment.organization_id)
            for population_id in experiment.population_ids:
                self.get_population(experiment.tenant_id, population_id)
            budget = experiment.execution_budget
            self._conn.execute(
                """
                INSERT INTO experiments(
                    tenant_id, id, organization_id, hypothesis, objective,
                    population_ids_json, random_seed, data_classification,
                    default_policy, execution_budget_json, variables_json,
                    launch_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, id) DO UPDATE SET
                    organization_id = excluded.organization_id,
                    hypothesis = excluded.hypothesis,
                    objective = excluded.objective,
                    population_ids_json = excluded.population_ids_json,
                    random_seed = excluded.random_seed,
                    data_classification = excluded.data_classification,
                    default_policy = excluded.default_policy,
                    execution_budget_json = excluded.execution_budget_json,
                    variables_json = excluded.variables_json,
                    launch_json = excluded.launch_json
                """,
                (
                    experiment.tenant_id.value,
                    experiment.id.value,
                    experiment.organization_id.value,
                    experiment.hypothesis,
                    experiment.objective,
                    _json_dumps([item.value for item in experiment.population_ids]),
                    experiment.random_seed,
                    experiment.data_classification.value,
                    experiment.default_policy.value,
                    _json_dumps(
                        {
                            "max_tokens": budget.max_tokens,
                            "max_cost": budget.max_cost,
                            "max_duration_seconds": budget.max_duration_seconds,
                            "max_concurrency": budget.max_concurrency,
                        }
                    ),
                    _json_dumps(experiment.variables),
                    _json_dumps(experiment.launch_payload()),
                ),
            )
            self._conn.commit()
            return experiment

    def get_experiment(
        self, tenant_id: TenantId, experiment_id: ExperimentId
    ) -> Experiment:
        _require_tenant(tenant_id, experiment_id)
        with self._lock:
            self._require_known_tenant(tenant_id)
            row = self._conn.execute(
                "SELECT * FROM experiments WHERE tenant_id = ? AND id = ?",
                (tenant_id.value, experiment_id.value),
            ).fetchone()
            if row is None:
                raise EntityNotFoundError(f"unknown experiment {experiment_id.value}")
            tenant = TenantId(row["tenant_id"])
            budget = json.loads(row["execution_budget_json"])
            population_ids = tuple(
                PopulationId(tenant, value)
                for value in json.loads(row["population_ids_json"])
            )
            launch_raw = row["launch_json"] if "launch_json" in row.keys() else None
            base = Experiment(
                id=ExperimentId(tenant, row["id"]),
                tenant_id=tenant,
                organization_id=OrganizationId(tenant, row["organization_id"]),
                hypothesis=row["hypothesis"],
                objective=row["objective"],
                population_ids=population_ids,
                random_seed=row["random_seed"],
                data_classification=DataClassification(row["data_classification"]),
                default_policy=PolicyDecision(row["default_policy"]),
                execution_budget=ExecutionBudget(
                    max_tokens=budget.get("max_tokens"),
                    max_cost=budget.get("max_cost"),
                    max_duration_seconds=budget.get("max_duration_seconds"),
                    max_concurrency=budget.get("max_concurrency"),
                ),
                variables=json.loads(row["variables_json"]),
            )
            payload = json.loads(launch_raw) if launch_raw else {}
            return apply_launch_payload(base, payload)

    def list_experiments(self, tenant_id: TenantId) -> list[Experiment]:
        with self._lock:
            self._require_known_tenant(tenant_id)
            rows = self._conn.execute(
                "SELECT * FROM experiments WHERE tenant_id = ? ORDER BY id",
                (tenant_id.value,),
            ).fetchall()
            return [self.get_experiment(tenant_id, ExperimentId(tenant_id, row["id"])) for row in rows]

    def _require_organization(
        self, tenant_id: TenantId, organization_id: OrganizationId
    ) -> None:
        try:
            self.get_organization(tenant_id, organization_id)
        except CrossTenantAccessError:
            raise
        except EntityNotFoundError as exc:
            raise EntityNotFoundError(
                f"unknown organization {organization_id.value}"
            ) from exc

    def put_org_edge(self, edge: OrgEdge) -> OrgEdge:
        with self._lock:
            self._require_known_tenant(edge.tenant_id)
            self._require_organization(edge.tenant_id, edge.organization_id)
            require_org_node(self, edge.tenant_id, edge.source_kind, edge.source_id)
            require_org_node(self, edge.tenant_id, edge.target_kind, edge.target_id)
            try:
                self._conn.execute(
                    """
                    INSERT INTO org_edges(
                        tenant_id, id, organization_id, relation, source_kind,
                        source_id, target_kind, target_id, attributes_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, id) DO UPDATE SET
                        organization_id = excluded.organization_id,
                        relation = excluded.relation,
                        source_kind = excluded.source_kind,
                        source_id = excluded.source_id,
                        target_kind = excluded.target_kind,
                        target_id = excluded.target_id,
                        attributes_json = excluded.attributes_json
                    """,
                    (
                        edge.tenant_id.value,
                        edge.id.value,
                        edge.organization_id.value,
                        edge.relation.value,
                        edge.source_kind.value,
                        edge.source_id,
                        edge.target_kind.value,
                        edge.target_id,
                        _json_dumps(edge.attributes),
                    ),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                raise EnterpriseSchemaError("duplicate org edge") from exc
            return edge

    def get_org_edge(self, tenant_id: TenantId, edge_id: OrgEdgeId) -> OrgEdge:
        _require_tenant(tenant_id, edge_id)
        with self._lock:
            self._require_known_tenant(tenant_id)
            row = self._conn.execute(
                "SELECT * FROM org_edges WHERE tenant_id = ? AND id = ?",
                (tenant_id.value, edge_id.value),
            ).fetchone()
            if row is None:
                raise EntityNotFoundError(f"unknown org edge {edge_id.value}")
            return self._org_edge_from_row(row)

    def list_org_edges(
        self,
        tenant_id: TenantId,
        *,
        organization_id: OrganizationId | None = None,
        relation: OrgRelation | None = None,
    ) -> list[OrgEdge]:
        with self._lock:
            self._require_known_tenant(tenant_id)
            sql = "SELECT * FROM org_edges WHERE tenant_id = ?"
            params: list[Any] = [tenant_id.value]
            if organization_id is not None:
                _require_tenant(tenant_id, organization_id)
                sql += " AND organization_id = ?"
                params.append(organization_id.value)
            if relation is not None:
                sql += " AND relation = ?"
                params.append(relation.value)
            sql += " ORDER BY id"
            rows = self._conn.execute(sql, params).fetchall()
            return [self._org_edge_from_row(row) for row in rows]

    def delete_org_edge(self, tenant_id: TenantId, edge_id: OrgEdgeId) -> None:
        self.get_org_edge(tenant_id, edge_id)
        with self._lock:
            self._conn.execute(
                "DELETE FROM org_edges WHERE tenant_id = ? AND id = ?",
                (tenant_id.value, edge_id.value),
            )
            self._conn.commit()

    def _org_edge_from_row(self, row: sqlite3.Row) -> OrgEdge:
        tenant = TenantId(row["tenant_id"])
        return OrgEdge(
            id=OrgEdgeId(tenant, row["id"]),
            tenant_id=tenant,
            organization_id=OrganizationId(tenant, row["organization_id"]),
            relation=OrgRelation(row["relation"]),
            source_kind=OrgNodeKind(row["source_kind"]),
            source_id=row["source_id"],
            target_kind=OrgNodeKind(row["target_kind"]),
            target_id=row["target_id"],
            attributes=json.loads(row["attributes_json"]),
        )

    def put_population_declaration(
        self, declaration: PopulationDeclaration
    ) -> PopulationDeclaration:
        with self._lock:
            self._require_known_tenant(declaration.tenant_id)
            self.get_population(declaration.tenant_id, declaration.population_id)
            self._require_organization(
                declaration.tenant_id, declaration.organization_id
            )
            segments = [
                {
                    "name": segment.name,
                    "count": segment.count,
                    "share": segment.share,
                    "filters": {
                        key: list(vals) for key, vals in segment.filters.items()
                    },
                    "constraints": dict(segment.constraints),
                }
                for segment in declaration.segments
            ]
            self._conn.execute(
                """
                INSERT INTO population_declarations(
                    tenant_id, population_id, organization_id, target_size,
                    backend, segments_json, constraints_json,
                    include_org_structure, privacy_mode, resolved_counts_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, population_id) DO UPDATE SET
                    organization_id = excluded.organization_id,
                    target_size = excluded.target_size,
                    backend = excluded.backend,
                    segments_json = excluded.segments_json,
                    constraints_json = excluded.constraints_json,
                    include_org_structure = excluded.include_org_structure,
                    privacy_mode = excluded.privacy_mode,
                    resolved_counts_json = excluded.resolved_counts_json
                """,
                (
                    declaration.tenant_id.value,
                    declaration.population_id.value,
                    declaration.organization_id.value,
                    declaration.target_size,
                    declaration.backend.value,
                    _json_dumps(segments),
                    _json_dumps(list(declaration.constraints)),
                    1 if declaration.include_org_structure else 0,
                    declaration.privacy_mode,
                    _json_dumps(list(declaration.resolved_counts)),
                ),
            )
            self._conn.commit()
            return declaration

    def get_population_declaration(
        self, tenant_id: TenantId, population_id: PopulationId
    ) -> PopulationDeclaration:
        _require_tenant(tenant_id, population_id)
        with self._lock:
            self._require_known_tenant(tenant_id)
            row = self._conn.execute(
                """
                SELECT * FROM population_declarations
                WHERE tenant_id = ? AND population_id = ?
                """,
                (tenant_id.value, population_id.value),
            ).fetchone()
            if row is None:
                raise EntityNotFoundError(
                    f"unknown population declaration {population_id.value}"
                )
            tenant = TenantId(row["tenant_id"])
            raw_segments = json.loads(row["segments_json"])
            segments = tuple(
                PopulationSegment(
                    name=item["name"],
                    count=item.get("count"),
                    share=item.get("share"),
                    filters=item.get("filters") or {},
                    constraints=item.get("constraints") or {},
                )
                for item in raw_segments
            )
            return PopulationDeclaration(
                tenant_id=tenant,
                organization_id=OrganizationId(tenant, row["organization_id"]),
                population_id=PopulationId(tenant, row["population_id"]),
                target_size=int(row["target_size"]),
                backend=GenerationBackend(row["backend"]),
                segments=segments,
                resolved_counts=tuple(json.loads(row["resolved_counts_json"])),
                constraints=tuple(json.loads(row["constraints_json"])),
                include_org_structure=bool(row["include_org_structure"]),
                privacy_mode=row["privacy_mode"],
            )

    def put_model_policy(self, policy: ModelPolicy) -> ModelPolicy:
        with self._lock:
            self._require_known_tenant(policy.tenant_id)
            self._conn.execute(
                """
                INSERT INTO tenant_model_policies(
                    tenant_id, allowed_providers_json, denied_providers_json,
                    required_residency, max_cost_score, max_latency_ms,
                    allowed_capabilities_json, allow_external, allow_live,
                    default_decision, denied_actions_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id) DO UPDATE SET
                    allowed_providers_json = excluded.allowed_providers_json,
                    denied_providers_json = excluded.denied_providers_json,
                    required_residency = excluded.required_residency,
                    max_cost_score = excluded.max_cost_score,
                    max_latency_ms = excluded.max_latency_ms,
                    allowed_capabilities_json = excluded.allowed_capabilities_json,
                    allow_external = excluded.allow_external,
                    allow_live = excluded.allow_live,
                    default_decision = excluded.default_decision,
                    denied_actions_json = excluded.denied_actions_json
                """,
                (
                    policy.tenant_id.value,
                    _json_dumps(list(policy.allowed_providers)),
                    _json_dumps(list(policy.denied_providers)),
                    policy.required_residency,
                    policy.max_cost_score,
                    policy.max_latency_ms,
                    _json_dumps(list(policy.allowed_capabilities)),
                    1 if policy.allow_external else 0,
                    1 if policy.allow_live else 0,
                    policy.default_decision.value,
                    _json_dumps(list(policy.denied_actions)),
                ),
            )
            self._conn.commit()
            return policy

    def get_model_policy(self, tenant_id: TenantId) -> ModelPolicy:
        with self._lock:
            self._require_known_tenant(tenant_id)
            row = self._conn.execute(
                "SELECT * FROM tenant_model_policies WHERE tenant_id = ?",
                (tenant_id.value,),
            ).fetchone()
            if row is None:
                return default_model_policy(tenant_id)
            return ModelPolicy(
                tenant_id=TenantId(row["tenant_id"]),
                allowed_providers=tuple(json.loads(row["allowed_providers_json"])),
                denied_providers=tuple(json.loads(row["denied_providers_json"])),
                required_residency=row["required_residency"],
                max_cost_score=row["max_cost_score"],
                max_latency_ms=row["max_latency_ms"],
                allowed_capabilities=tuple(json.loads(row["allowed_capabilities_json"])),
                allow_external=bool(row["allow_external"]),
                allow_live=bool(row["allow_live"]),
                default_decision=PolicyDecision(row["default_decision"]),
                denied_actions=tuple(json.loads(row["denied_actions_json"])),
            )
