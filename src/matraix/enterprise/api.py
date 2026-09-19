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
    Organization,
    Population,
    Tenant,
)
from matraix.enterprise.errors import (
    CrossTenantAccessError,
    EnterpriseSchemaError,
    EntityNotFoundError,
)
from matraix.enterprise.ids import (
    EntityKind,
    OrganizationId,
    PersonaId,
    PopulationId,
    TenantId,
    new_id,
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
            {"name": "personas", "description": "Wrappers around existing YAML records"},
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

    return app


app = create_enterprise_app()
