"""Versioned AgentTwin Enterprise control-plane API (``/api/v1``).

Standalone FastAPI app — does not replace Playground. Tenant isolation is
enforced by ``X-Tenant-Id`` plus the repository contract. When
``MATRIX_ENTERPRISE_API_TOKEN`` is set, requests must send
``Authorization: Bearer <token>``. The token is read from the environment only.
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from matraix.enterprise.entities import (
    EnterprisePersona,
    ExecutionBudget,
    ExperimentGovernance,
    Organization,
    Population,
    Tenant,
)
from matraix.enterprise.experiment_launch import (
    estimate_experiment_cost,
    experiment_from_parts,
    map_experiment_to_harbor_job,
)
from matraix.enterprise.graph import OrgEdge, OrgNodeKind, OrgRelation
from matraix.enterprise.errors import (
    CrossTenantAccessError,
    EnterpriseSchemaError,
    EntityNotFoundError,
)
from matraix.enterprise.ids import (
    EntityKind,
    ExperimentId,
    OrganizationId,
    OrgEdgeId,
    PersonaId,
    PopulationId,
    TenantId,
    new_id,
)
from matraix.enterprise.population_builder import (
    PopulationSegment,
    build_population_declaration,
)
from matraix.enterprise.repositories import EnterpriseRepository
from matraix.enterprise.store import (
    create_tenant_with_default_org,
    default_organization_for,
    open_enterprise_store,
)

API_TOKEN_ENV = "MATRIX_ENTERPRISE_API_TOKEN"
TENANT_HEADER = "X-Tenant-Id"


class TenantCreate(BaseModel):
    name: str = Field(..., examples=["Acme Research"])
    slug: str = Field(..., examples=["acme"])


class OrganizationOut(BaseModel):
    id: str
    tenant_id: str
    name: str
    parent_id: str | None = None
    industry: str | None = None
    geography: str | None = None


class TenantOut(BaseModel):
    id: str
    name: str
    slug: str
    created_at: str
    default_organization_id: str | None = None
    organizations: list[OrganizationOut] = Field(default_factory=list)


class PopulationCreate(BaseModel):
    name: str
    description: str | None = None
    target_size: int | None = None
    organization_id: str | None = None


class PopulationOut(BaseModel):
    id: str
    tenant_id: str
    organization_id: str
    name: str
    description: str | None = None
    target_size: int | None = None
    team_id: str | None = None


class PersonaCreate(BaseModel):
    """Wrap an existing Playground/Harbor persona YAML mapping."""

    record: dict[str, Any]
    organization_id: str | None = None
    population_id: str | None = None


class PersonaOut(BaseModel):
    id: str
    tenant_id: str
    organization_id: str
    population_id: str | None = None
    legacy_persona_id: str
    version: str
    source: str
    display_name: str | None = None
    dimensions: dict[str, str]
    enterprise: dict[str, dict[str, str]] | None = None
    data_classification: str


class OrgEdgeCreate(BaseModel):
    relation: str
    source_kind: str
    source_id: str
    target_kind: str
    target_id: str
    organization_id: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class OrgEdgeOut(BaseModel):
    id: str
    tenant_id: str
    organization_id: str
    relation: str
    source_kind: str
    source_id: str
    target_kind: str
    target_id: str
    attributes: dict[str, str] = Field(default_factory=dict)


class SegmentIn(BaseModel):
    name: str
    count: int | None = None
    share: float | None = None
    filters: dict[str, list[str]] = Field(default_factory=dict)
    constraints: dict[str, str] = Field(default_factory=dict)


class PopulationDeclarationIn(BaseModel):
    target_size: int = Field(..., examples=[10000])
    backend: str = Field(..., examples=["coreset_1m"])
    segments: list[SegmentIn]
    constraints: list[str] = Field(default_factory=list)
    include_org_structure: bool = False
    privacy_mode: str = "aggregate_stats_then_synthetic"
    organization_id: str | None = None
    name: str | None = Field(
        default=None,
        description="When creating a population with the declaration, the population name.",
    )
    description: str | None = None


class ExperimentCreate(BaseModel):
    hypothesis: str
    objective: str
    population_ids: list[str] = Field(default_factory=list)
    random_seed: int | None = 42
    kind: str = "baseline"
    task_path: str | None = None
    model_name: str | None = None
    agent_name: str | None = None
    metrics: list[str] = Field(default_factory=list)
    sample_size: int | None = None
    n_attempts: int = 1
    trial_profile: str = "json_survey"
    execution_mode: str = "auto"
    data_classification: str = "INTERNAL"
    default_policy: str = "SANDBOX_ONLY"
    organization_id: str | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    execution_budget: dict[str, Any] = Field(default_factory=dict)
    governance: dict[str, Any] = Field(default_factory=dict)


def _configured_token() -> str | None:
    token = os.environ.get(API_TOKEN_ENV, "").strip()
    return token or None


def _require_token(request: Request) -> None:
    expected = _configured_token()
    if expected is None:
        return
    header = request.headers.get("authorization") or ""
    prefix = "bearer "
    got = header[len(prefix) :].strip() if header.lower().startswith(prefix) else ""
    if not got or got != expected:
        raise HTTPException(status_code=401, detail="invalid or missing API token")


def _parse_tenant_id(raw: str) -> TenantId:
    try:
        return TenantId(raw)
    except EnterpriseSchemaError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _require_tenant_header(x_tenant_id: str | None) -> TenantId:
    if not x_tenant_id or not str(x_tenant_id).strip():
        raise HTTPException(
            status_code=400, detail=f"{TENANT_HEADER} header is required"
        )
    return _parse_tenant_id(x_tenant_id)


def _organization_out(organization: Organization) -> OrganizationOut:
    return OrganizationOut(
        id=organization.id.value,
        tenant_id=organization.tenant_id.value,
        name=organization.name,
        parent_id=organization.parent_id.value if organization.parent_id else None,
        industry=organization.industry,
        geography=organization.geography,
    )


def _tenant_out(store: EnterpriseRepository, tenant: Tenant) -> TenantOut:
    organizations = store.list_organizations(tenant.id)
    default_id = organizations[0].id.value if organizations else None
    return TenantOut(
        id=tenant.id.value,
        name=tenant.name,
        slug=tenant.slug,
        created_at=tenant.created_at.isoformat(),
        default_organization_id=default_id,
        organizations=[_organization_out(item) for item in organizations],
    )


def _population_out(population: Population) -> PopulationOut:
    return PopulationOut(
        id=population.id.value,
        tenant_id=population.tenant_id.value,
        organization_id=population.organization_id.value,
        name=population.name,
        description=population.description,
        target_size=population.target_size,
        team_id=population.team_id.value if population.team_id else None,
    )


def _org_edge_out(edge: OrgEdge) -> OrgEdgeOut:
    return OrgEdgeOut(
        id=edge.id.value,
        tenant_id=edge.tenant_id.value,
        organization_id=edge.organization_id.value,
        relation=edge.relation.value,
        source_kind=edge.source_kind.value,
        source_id=edge.source_id,
        target_kind=edge.target_kind.value,
        target_id=edge.target_id,
        attributes=dict(edge.attributes),
    )


def _segments_from_payload(payload: list[SegmentIn]) -> list[PopulationSegment]:
    return [
        PopulationSegment(
            name=item.name,
            count=item.count,
            share=item.share,
            filters=item.filters,
            constraints=item.constraints,
        )
        for item in payload
    ]


def _persona_out(persona: EnterprisePersona) -> PersonaOut:
    return PersonaOut(
        id=persona.id.value,
        tenant_id=persona.tenant_id.value,
        organization_id=persona.organization_id.value,
        population_id=persona.population_id.value if persona.population_id else None,
        legacy_persona_id=persona.legacy_persona_id,
        version=persona.version,
        source=persona.source,
        display_name=persona.display_name,
        dimensions=dict(persona.dimensions),
        enterprise=persona.enterprise.to_dict() if persona.enterprise else None,
        data_classification=persona.data_classification.value,
    )


def _resolve_organization_id(
    store: EnterpriseRepository, tenant_id: TenantId, raw: str | None
) -> OrganizationId:
    if raw:
        return OrganizationId(tenant_id, raw)
    return default_organization_for(store, tenant_id).id


def create_enterprise_app(
    store: EnterpriseRepository | None = None,
) -> FastAPI:
    repository = store or open_enterprise_store()

    app = FastAPI(
        title="AgentTwin Enterprise API",
        version="v1",
        description=(
            "Tenant-scoped control plane for tenants, populations, and personas. "
            "Does not replace Playground or Harbor. Synthetic personas are "
            "simulation parameters, not psychological equivalents of humans."
        ),
        openapi_tags=[
            {"name": "tenants", "description": "Isolation boundaries"},
            {"name": "organizations", "description": "Business units under a tenant"},
            {"name": "populations", "description": "Named persona sets"},
            {
                "name": "population-declarations",
                "description": "Shaped population builder (does not rewrite persona/synthesis)",
            },
            {"name": "org-edges", "description": "Organizational graph relationships"},
            {"name": "personas", "description": "Wrappers around existing YAML records"},
            {
                "name": "experiments",
                "description": "Launch records mapped onto Harbor job YAML (does not replace Job)",
            },
        ],
    )
    app.state.store = repository

    @app.exception_handler(CrossTenantAccessError)
    async def _cross_tenant(_request: Request, exc: CrossTenantAccessError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(EntityNotFoundError)
    async def _not_found(_request: Request, exc: EntityNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(EnterpriseSchemaError)
    async def _schema(_request: Request, exc: EnterpriseSchemaError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.middleware("http")
    async def _auth_middleware(request: Request, call_next):
        if request.url.path in {"/docs", "/redoc", "/openapi.json"}:
            return await call_next(request)
        try:
            _require_token(request)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code, content={"detail": exc.detail}
            )
        return await call_next(request)

    def _store() -> EnterpriseRepository:
        return app.state.store

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/v1/tenants", response_model=TenantOut, tags=["tenants"])
    def create_tenant(payload: TenantCreate) -> TenantOut:
        tenant, _organization = create_tenant_with_default_org(
            _store(), name=payload.name, slug=payload.slug
        )
        return _tenant_out(_store(), tenant)

    @app.get("/api/v1/tenants", response_model=list[TenantOut], tags=["tenants"])
    def list_tenants() -> list[TenantOut]:
        return [_tenant_out(_store(), tenant) for tenant in _store().list_tenants()]

    @app.get("/api/v1/tenants/{tenant_id}", response_model=TenantOut, tags=["tenants"])
    def get_tenant(
        tenant_id: str,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> TenantOut:
        resolved = _parse_tenant_id(tenant_id)
        if x_tenant_id and _parse_tenant_id(x_tenant_id) != resolved:
            raise HTTPException(
                status_code=403, detail="X-Tenant-Id does not match path tenant"
            )
        return _tenant_out(_store(), _store().get_tenant(resolved))

    @app.get(
        "/api/v1/organizations",
        response_model=list[OrganizationOut],
        tags=["organizations"],
    )
    def list_organizations(
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> list[OrganizationOut]:
        tenant = _require_tenant_header(x_tenant_id)
        return [_organization_out(item) for item in _store().list_organizations(tenant)]

    @app.post("/api/v1/populations", response_model=PopulationOut, tags=["populations"])
    def create_population(
        payload: PopulationCreate,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> PopulationOut:
        tenant = _require_tenant_header(x_tenant_id)
        organization_id = _resolve_organization_id(
            _store(), tenant, payload.organization_id
        )
        population = Population(
            id=PopulationId(tenant, new_id(EntityKind.POPULATION)),
            tenant_id=tenant,
            organization_id=organization_id,
            name=payload.name,
            description=payload.description,
            target_size=payload.target_size,
        )
        return _population_out(_store().put_population(population))

    @app.get(
        "/api/v1/populations", response_model=list[PopulationOut], tags=["populations"]
    )
    def list_populations(
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> list[PopulationOut]:
        tenant = _require_tenant_header(x_tenant_id)
        return [_population_out(item) for item in _store().list_populations(tenant)]

    @app.get(
        "/api/v1/populations/{population_id}",
        response_model=PopulationOut,
        tags=["populations"],
    )
    def get_population(
        population_id: str,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> PopulationOut:
        tenant = _require_tenant_header(x_tenant_id)
        return _population_out(
            _store().get_population(tenant, PopulationId(tenant, population_id))
        )

    @app.post("/api/v1/personas", response_model=PersonaOut, tags=["personas"])
    def create_persona(
        payload: PersonaCreate,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> PersonaOut:
        tenant = _require_tenant_header(x_tenant_id)
        organization_id = _resolve_organization_id(
            _store(), tenant, payload.organization_id
        )
        population_id = (
            PopulationId(tenant, payload.population_id)
            if payload.population_id
            else None
        )
        persona = EnterprisePersona.from_legacy_record(
            tenant_id=tenant,
            organization_id=organization_id,
            record=payload.record,
            population_id=population_id,
        )
        return _persona_out(_store().put_persona(persona))

    @app.get("/api/v1/personas", response_model=list[PersonaOut], tags=["personas"])
    def list_personas(
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
        population_id: Annotated[str | None, Query()] = None,
    ) -> list[PersonaOut]:
        tenant = _require_tenant_header(x_tenant_id)
        items = _store().list_personas(tenant)
        if population_id:
            wanted = PopulationId(tenant, population_id)
            items = [
                item
                for item in items
                if item.population_id is not None and item.population_id == wanted
            ]
        return [_persona_out(item) for item in items]

    @app.get("/api/v1/personas/{persona_id}", response_model=PersonaOut, tags=["personas"])
    def get_persona(
        persona_id: str,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> PersonaOut:
        tenant = _require_tenant_header(x_tenant_id)
        return _persona_out(_store().get_persona(tenant, PersonaId(tenant, persona_id)))

    @app.post("/api/v1/org-edges", response_model=OrgEdgeOut, tags=["org-edges"])
    def create_org_edge(
        payload: OrgEdgeCreate,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> OrgEdgeOut:
        tenant = _require_tenant_header(x_tenant_id)
        organization_id = _resolve_organization_id(
            _store(), tenant, payload.organization_id
        )
        edge = OrgEdge(
            id=OrgEdgeId(tenant, new_id(EntityKind.ORG_EDGE)),
            tenant_id=tenant,
            organization_id=organization_id,
            relation=payload.relation,
            source_kind=payload.source_kind,
            source_id=payload.source_id,
            target_kind=payload.target_kind,
            target_id=payload.target_id,
            attributes=payload.attributes,
        )
        return _org_edge_out(_store().put_org_edge(edge))

    @app.get("/api/v1/org-edges", response_model=list[OrgEdgeOut], tags=["org-edges"])
    def list_org_edges(
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
        relation: Annotated[str | None, Query()] = None,
    ) -> list[OrgEdgeOut]:
        tenant = _require_tenant_header(x_tenant_id)
        parsed_relation = None
        if relation:
            try:
                parsed_relation = OrgRelation(relation.strip().lower())
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail=f"unknown org relation {relation!r}"
                ) from exc
        return [
            _org_edge_out(item)
            for item in _store().list_org_edges(tenant, relation=parsed_relation)
        ]

    @app.get("/api/v1/org-edges/{edge_id}", response_model=OrgEdgeOut, tags=["org-edges"])
    def get_org_edge(
        edge_id: str,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> OrgEdgeOut:
        tenant = _require_tenant_header(x_tenant_id)
        return _org_edge_out(_store().get_org_edge(tenant, OrgEdgeId(tenant, edge_id)))

    @app.delete("/api/v1/org-edges/{edge_id}", status_code=204, tags=["org-edges"])
    def delete_org_edge(
        edge_id: str,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> None:
        tenant = _require_tenant_header(x_tenant_id)
        _store().delete_org_edge(tenant, OrgEdgeId(tenant, edge_id))

    def _put_declaration(
        tenant: TenantId,
        population: Population,
        payload: PopulationDeclarationIn,
    ) -> dict[str, Any]:
        declaration = build_population_declaration(
            tenant_id=tenant,
            organization_id=population.organization_id,
            population_id=population.id,
            target_size=payload.target_size,
            backend=payload.backend,
            segments=_segments_from_payload(payload.segments),
            constraints=payload.constraints,
            include_org_structure=payload.include_org_structure,
            privacy_mode=payload.privacy_mode,
        )
        stored = _store().put_population_declaration(declaration)
        return stored.to_dict()

    @app.post(
        "/api/v1/population-declarations",
        tags=["population-declarations"],
    )
    def create_population_declaration(
        payload: PopulationDeclarationIn,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        organization_id = _resolve_organization_id(
            _store(), tenant, payload.organization_id
        )
        name = payload.name or f"{payload.target_size}-shaped population"
        population = Population(
            id=PopulationId(tenant, new_id(EntityKind.POPULATION)),
            tenant_id=tenant,
            organization_id=organization_id,
            name=name,
            description=payload.description,
            target_size=payload.target_size,
        )
        stored_pop = _store().put_population(population)
        return _put_declaration(tenant, stored_pop, payload)

    @app.put(
        "/api/v1/populations/{population_id}/declaration",
        tags=["population-declarations"],
    )
    def put_population_declaration(
        population_id: str,
        payload: PopulationDeclarationIn,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        population = _store().get_population(
            tenant, PopulationId(tenant, population_id)
        )
        return _put_declaration(tenant, population, payload)

    @app.get(
        "/api/v1/populations/{population_id}/declaration",
        tags=["population-declarations"],
    )
    def get_population_declaration(
        population_id: str,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        declaration = _store().get_population_declaration(
            tenant, PopulationId(tenant, population_id)
        )
        return declaration.to_dict()

    def _budget_from_payload(raw: dict[str, Any]) -> ExecutionBudget:
        return ExecutionBudget(
            max_tokens=raw.get("max_tokens"),
            max_cost=raw.get("max_cost"),
            max_duration_seconds=raw.get("max_duration_seconds"),
            max_concurrency=raw.get("max_concurrency"),
        )

    def _governance_from_payload(raw: dict[str, Any]) -> ExperimentGovernance:
        return ExperimentGovernance(
            retention_days=raw.get("retention_days"),
            requires_human_validation=raw.get("requires_human_validation", True),
            sign_off=raw.get("sign_off"),
            notes=raw.get("notes"),
            limitations_required=raw.get("limitations_required", True),
        )

    def _population_target(tenant, population_ids) -> int | None:
        if not population_ids:
            return None
        try:
            pop = _store().get_population(tenant, population_ids[0])
        except EntityNotFoundError:
            return None
        return pop.target_size

    def _persona_paths(tenant, population_ids) -> list[str]:
        paths: list[str] = []
        wanted = {item.value for item in population_ids}
        for persona in _store().list_personas(tenant):
            if persona.population_id and persona.population_id.value in wanted:
                paths.append(f"legacy:{persona.legacy_persona_id}")
        return paths

    @app.post("/api/v1/experiments", tags=["experiments"])
    def create_experiment(
        payload: ExperimentCreate,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        organization_id = _resolve_organization_id(
            _store(), tenant, payload.organization_id
        )
        population_ids = tuple(
            PopulationId(tenant, item) for item in payload.population_ids
        )
        experiment = experiment_from_parts(
            tenant_id=tenant,
            organization_id=organization_id,
            hypothesis=payload.hypothesis,
            objective=payload.objective,
            population_ids=population_ids,
            random_seed=payload.random_seed,
            data_classification=payload.data_classification,
            default_policy=payload.default_policy,
            execution_budget=_budget_from_payload(payload.execution_budget),
            variables=payload.variables,
            kind=payload.kind,
            task_path=payload.task_path,
            model_name=payload.model_name,
            agent_name=payload.agent_name,
            metrics=payload.metrics,
            governance=_governance_from_payload(payload.governance),
            sample_size=payload.sample_size,
            n_attempts=payload.n_attempts,
            trial_profile=payload.trial_profile,
            execution_mode=payload.execution_mode,
        )
        return _store().put_experiment(experiment).to_dict()

    @app.get("/api/v1/experiments", tags=["experiments"])
    def list_experiments(
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> list[dict[str, Any]]:
        tenant = _require_tenant_header(x_tenant_id)
        return [item.to_dict() for item in _store().list_experiments(tenant)]

    @app.get("/api/v1/experiments/{experiment_id}", tags=["experiments"])
    def get_experiment(
        experiment_id: str,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        return _store().get_experiment(
            tenant, ExperimentId(tenant, experiment_id)
        ).to_dict()

    @app.post("/api/v1/experiments/{experiment_id}/estimate", tags=["experiments"])
    def estimate_experiment(
        experiment_id: str,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        experiment = _store().get_experiment(
            tenant, ExperimentId(tenant, experiment_id)
        )
        return estimate_experiment_cost(
            experiment,
            population_target=_population_target(tenant, experiment.population_ids),
        ).to_dict()

    @app.get("/api/v1/experiments/{experiment_id}/harbor-job", tags=["experiments"])
    def experiment_harbor_job(
        experiment_id: str,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        experiment = _store().get_experiment(
            tenant, ExperimentId(tenant, experiment_id)
        )
        return map_experiment_to_harbor_job(
            experiment,
            persona_paths=_persona_paths(tenant, experiment.population_ids),
        )

    return app


app = create_enterprise_app()
