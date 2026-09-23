"""Versioned AgentTwin Enterprise control-plane API (``/api/v1``).

Standalone FastAPI app — does not replace Playground (it can also be mounted
into the Playground backend, see :func:`mount_enterprise_api`).

Request pipeline (all requests):

1. ``X-Request-Id`` is honoured or generated and echoed back.
2. The configured :class:`~matraix.enterprise.auth.ChainAuthProvider` resolves a
   :class:`~matraix.enterprise.auth.Principal`; ``/docs``, ``/openapi.json``,
   ``/health`` and ``/ready`` are public. No principal → ``401``.
3. Handlers derive the tenant from the principal (``X-Tenant-Id`` only widens
   for platform admins; a mismatch is ``403``) and call :func:`guard` for the
   required permission. Every mutation writes an audit row.
4. Errors return a stable object ``{"detail", "error": {"code", "message",
   "request_id"}}``.

Backward compatibility: the Phase 1–2 routes, payloads and the shared
``MATRIX_ENTERPRISE_API_TOKEN`` (now a platform-admin credential) keep working.
When no credential is configured the API stays open for local development.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from matraix.enterprise.audit import AuditCategory
from matraix.enterprise.auth.principal import Principal
from matraix.enterprise.auth.roles import AuthenticationError, AuthorizationError, Permission
from matraix.enterprise.context import EnterpriseContext, build_context
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
from matraix.enterprise.graph import OrgEdge, OrgRelation
from matraix.enterprise.ids import (
    EntityKind,
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
from matraix.enterprise.records import ConcurrencyError
from matraix.enterprise.repositories import EnterpriseRepository
from matraix.enterprise.routes.common import (
    REQUEST_ID_HEADER,
    TENANT_HEADER,
    Page,
    current_principal,
    error_body,
    guard,
    paginate,
    parse_tenant_id,
    request_id_of,
    tenant_scope,
)
from matraix.enterprise.store import (
    create_tenant_with_default_org,
    default_organization_for,
)

API_TOKEN_ENV = "MATRIX_ENTERPRISE_API_TOKEN"
PUBLIC_PATHS = frozenset({"/docs", "/redoc", "/openapi.json", "/health", "/ready"})

logger = logging.getLogger("matraix.enterprise.api")


# --------------------------------------------------------------------------- #
# Legacy IO models (unchanged shapes)
# --------------------------------------------------------------------------- #


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


class OrganizationCreate(BaseModel):
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


# --------------------------------------------------------------------------- #
# Serializers
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# App factory
# --------------------------------------------------------------------------- #


def _install_error_handlers(app: FastAPI) -> None:
    def _json(status: int, code: str, message: str, request: Request) -> JSONResponse:
        response = JSONResponse(
            status_code=status,
            content=error_body(code, message, request_id_of(request)),
        )
        rid = request_id_of(request)
        if rid:
            response.headers[REQUEST_ID_HEADER] = rid
        return response

    @app.exception_handler(CrossTenantAccessError)
    async def _cross_tenant(request: Request, exc: CrossTenantAccessError) -> JSONResponse:
        return _json(403, "cross_tenant_access", str(exc), request)

    @app.exception_handler(AuthorizationError)
    async def _forbidden(request: Request, exc: AuthorizationError) -> JSONResponse:
        return _json(403, "forbidden", str(exc), request)

    @app.exception_handler(AuthenticationError)
    async def _unauthenticated(request: Request, exc: AuthenticationError) -> JSONResponse:
        return _json(401, "unauthenticated", str(exc), request)

    @app.exception_handler(EntityNotFoundError)
    async def _not_found(request: Request, exc: EntityNotFoundError) -> JSONResponse:
        message = exc.args[0] if exc.args else str(exc)
        return _json(404, "not_found", str(message), request)

    @app.exception_handler(EnterpriseSchemaError)
    async def _schema(request: Request, exc: EnterpriseSchemaError) -> JSONResponse:
        return _json(400, "invalid_request", str(exc), request)

    @app.exception_handler(ConcurrencyError)
    async def _conflict(request: Request, exc: ConcurrencyError) -> JSONResponse:
        return _json(409, "conflict", str(exc), request)

    @app.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException) -> JSONResponse:
        codes = {400: "invalid_request", 401: "unauthenticated", 403: "forbidden", 404: "not_found", 409: "conflict", 422: "validation_error"}
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        response = _json(exc.status_code, codes.get(exc.status_code, "error"), detail, request)
        if exc.headers:
            for key, value in exc.headers.items():
                response.headers[key] = value
        return response


def _install_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def _request_pipeline(request: Request, call_next):
        request.state.request_id = request.headers.get(REQUEST_ID_HEADER) or f"req_{uuid.uuid4().hex[:16]}"
        ctx: EnterpriseContext = request.app.state.context
        path = request.url.path
        request.state.principal = None
        if path not in PUBLIC_PATHS and not path.startswith("/docs"):
            try:
                principal = ctx.auth.authenticate(request.headers)
            except AuthenticationError as exc:
                return _error_response(request, 401, "unauthenticated", str(exc))
            if principal is None:
                return _error_response(request, 401, "unauthenticated", "invalid or missing API token")
            request.state.principal = principal
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        return response


def _error_response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    response = JSONResponse(status_code=status, content=error_body(code, message, request_id_of(request)))
    rid = request_id_of(request)
    if rid:
        response.headers[REQUEST_ID_HEADER] = rid
    return response


def _include_routers(app: FastAPI) -> None:
    """Attach every available router.

    A module that exists but fails to import is a **real error** and propagates:
    silently skipping it would hide a broken endpoint in production while tests
    that import the module directly still pass. Routers for later phases are not
    in the tree yet, so they are probed with ``find_spec`` and skipped only when
    genuinely absent.
    """
    import importlib
    import importlib.util

    required = ("identity", "audit", "populations")
    later_phases = ("graph", "experiments", "runs", "governance", "metrics")

    for module_name in required:
        module = importlib.import_module(f"matraix.enterprise.routes.{module_name}")
        app.include_router(module.router)

    for module_name in later_phases:
        qualified = f"matraix.enterprise.routes.{module_name}"
        if importlib.util.find_spec(qualified) is None:
            continue
        module = importlib.import_module(qualified)
        app.include_router(module.router)


def create_enterprise_app(
    store: EnterpriseRepository | None = None,
    *,
    context: EnterpriseContext | None = None,
) -> FastAPI:
    ctx = context or build_context(store)
    repository = ctx.store

    app = FastAPI(
        title="AgentTwin Enterprise API",
        version="v1",
        description=(
            "Tenant-scoped control plane for tenants, identities, populations, "
            "organizational graphs, experiments, runs, governance, metrics, reports "
            "and audit. Does not replace Playground or Harbor. Simulated users are "
            "controlled experimental instruments, not validated replacements for "
            "evidence from real human populations."
        ),
        openapi_tags=[
            {"name": "tenants", "description": "Isolation boundaries"},
            {"name": "organizations", "description": "Business units under a tenant"},
            {"name": "identities", "description": "Users, service accounts, role bindings"},
            {"name": "populations", "description": "Named persona sets, versions, cohorts"},
            {
                "name": "population-declarations",
                "description": "Shaped population builder (does not rewrite persona/synthesis)",
            },
            {"name": "org-edges", "description": "Organizational graph relationships"},
            {"name": "org-graph", "description": "Organizational nodes and visualization payloads"},
            {"name": "personas", "description": "Wrappers around existing YAML records"},
            {"name": "experiments", "description": "Experiment definitions, versions, lifecycle"},
            {"name": "runs", "description": "Runs, trials, work queue"},
            {"name": "governance", "description": "Policies, budgets, quotas, approvals, models"},
            {"name": "metrics", "description": "Metrics and reports"},
            {"name": "audit", "description": "Append-only security audit stream"},
        ],
    )
    app.state.store = repository
    app.state.context = ctx

    _install_error_handlers(app)
    _install_middleware(app)

    def _store() -> EnterpriseRepository:
        return app.state.store

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok", "service": ctx.settings.service_name}

    @app.get("/ready", include_in_schema=False)
    def ready() -> JSONResponse:
        try:
            ctx.store.list_tenants()
        except Exception as exc:  # noqa: BLE001 - readiness must report, not raise
            return JSONResponse(status_code=503, content={"status": "unavailable", "reason": exc.__class__.__name__})
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "store": type(ctx.store).__name__, "auth_open": ctx.auth.open},
        )

    # ---------------------------------------------------------------- tenants
    @app.post("/api/v1/tenants", response_model=TenantOut, tags=["tenants"])
    def create_tenant(
        payload: TenantCreate,
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> TenantOut:
        if not principal.platform_admin:
            raise AuthorizationError("only platform administrators can create tenants")
        tenant, _organization = create_tenant_with_default_org(
            _store(), name=payload.name, slug=payload.slug
        )
        ctx.audit.record(
            tenant_id=tenant.id.value,
            principal=principal,
            action="tenant.create",
            category=AuditCategory.ADMIN,
            resource_type="tenant",
            resource_id=tenant.id.value,
            request_id=request_id_of(request),
            details={"slug": tenant.slug},
        )
        return _tenant_out(_store(), tenant)

    @app.get("/api/v1/tenants", tags=["tenants"])
    def list_tenants(
        principal: Annotated[Principal, Depends(current_principal)],
        page: Annotated[Page, Depends()],
    ) -> Any:
        tenants = _store().list_tenants()
        if not principal.platform_admin:
            tenants = [item for item in tenants if principal.tenant_id and item.id.value == principal.tenant_id]
        return paginate([_tenant_out(_store(), tenant) for tenant in tenants], page)

    @app.get("/api/v1/tenants/{tenant_id}", response_model=TenantOut, tags=["tenants"])
    def get_tenant(
        tenant_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> TenantOut:
        resolved = parse_tenant_id(tenant_id)
        if x_tenant_id and parse_tenant_id(x_tenant_id) != resolved:
            raise HTTPException(
                status_code=403, detail="X-Tenant-Id does not match path tenant"
            )
        guard(request, ctx, principal, Permission.TENANT_READ, resolved)
        return _tenant_out(_store(), _store().get_tenant(resolved))

    # ---------------------------------------------------------- organizations
    @app.get("/api/v1/organizations", tags=["organizations"])
    def list_organizations(
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
        tenant: Annotated[TenantId, Depends(tenant_scope)],
        page: Annotated[Page, Depends()],
    ) -> Any:
        guard(request, ctx, principal, Permission.ORGANIZATION_READ, tenant)
        return paginate([_organization_out(item) for item in _store().list_organizations(tenant)], page)

    @app.post("/api/v1/organizations", response_model=OrganizationOut, tags=["organizations"])
    def create_organization(
        payload: OrganizationCreate,
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
        tenant: Annotated[TenantId, Depends(tenant_scope)],
    ) -> OrganizationOut:
        guard(request, ctx, principal, Permission.ORGANIZATION_WRITE, tenant)
        organization = Organization(
            id=OrganizationId(tenant, new_id(EntityKind.ORGANIZATION)),
            tenant_id=tenant,
            name=payload.name,
            parent_id=OrganizationId(tenant, payload.parent_id) if payload.parent_id else None,
            industry=payload.industry,
            geography=payload.geography,
        )
        if organization.parent_id is not None:
            _store().get_organization(tenant, organization.parent_id)
        stored = _store().put_organization(organization)
        ctx.audit.record(
            tenant_id=tenant.value,
            principal=principal,
            action="organization.create",
            category=AuditCategory.RESOURCE_CREATE,
            resource_type="organization",
            resource_id=stored.id.value,
            request_id=request_id_of(request),
        )
        return _organization_out(stored)

    # ------------------------------------------------------------ populations
    @app.post("/api/v1/populations", response_model=PopulationOut, tags=["populations"])
    def create_population(
        payload: PopulationCreate,
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
        tenant: Annotated[TenantId, Depends(tenant_scope)],
    ) -> PopulationOut:
        guard(request, ctx, principal, Permission.POPULATION_WRITE, tenant)
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
        stored = _store().put_population(population)
        ctx.audit.record(
            tenant_id=tenant.value,
            principal=principal,
            action="population.create",
            category=AuditCategory.RESOURCE_CREATE,
            resource_type="population",
            resource_id=stored.id.value,
            request_id=request_id_of(request),
        )
        return _population_out(stored)

    @app.get("/api/v1/populations", tags=["populations"])
    def list_populations(
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
        tenant: Annotated[TenantId, Depends(tenant_scope)],
        page: Annotated[Page, Depends()],
    ) -> Any:
        guard(request, ctx, principal, Permission.POPULATION_READ, tenant)
        return paginate([_population_out(item) for item in _store().list_populations(tenant)], page)

    @app.get(
        "/api/v1/populations/{population_id}",
        response_model=PopulationOut,
        tags=["populations"],
    )
    def get_population(
        population_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
        tenant: Annotated[TenantId, Depends(tenant_scope)],
    ) -> PopulationOut:
        guard(request, ctx, principal, Permission.POPULATION_READ, tenant)
        return _population_out(
            _store().get_population(tenant, PopulationId(tenant, population_id))
        )

    # --------------------------------------------------------------- personas
    @app.post("/api/v1/personas", response_model=PersonaOut, tags=["personas"])
    def create_persona(
        payload: PersonaCreate,
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
        tenant: Annotated[TenantId, Depends(tenant_scope)],
    ) -> PersonaOut:
        guard(request, ctx, principal, Permission.POPULATION_WRITE, tenant)
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
        stored = _store().put_persona(persona)
        ctx.audit.record(
            tenant_id=tenant.value,
            principal=principal,
            action="persona.create",
            category=AuditCategory.RESOURCE_CREATE,
            resource_type="persona",
            resource_id=stored.id.value,
            request_id=request_id_of(request),
        )
        return _persona_out(stored)

    @app.get("/api/v1/personas", tags=["personas"])
    def list_personas(
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
        tenant: Annotated[TenantId, Depends(tenant_scope)],
        page: Annotated[Page, Depends()],
        population_id: Annotated[str | None, Query()] = None,
    ) -> Any:
        guard(request, ctx, principal, Permission.POPULATION_READ, tenant)
        items = _store().list_personas(tenant)
        if population_id:
            wanted = PopulationId(tenant, population_id)
            items = [
                item
                for item in items
                if item.population_id is not None and item.population_id == wanted
            ]
        return paginate([_persona_out(item) for item in items], page)

    @app.get("/api/v1/personas/{persona_id}", response_model=PersonaOut, tags=["personas"])
    def get_persona(
        persona_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
        tenant: Annotated[TenantId, Depends(tenant_scope)],
    ) -> PersonaOut:
        guard(request, ctx, principal, Permission.POPULATION_READ, tenant)
        return _persona_out(_store().get_persona(tenant, PersonaId(tenant, persona_id)))

    # -------------------------------------------------------------- org edges
    @app.post("/api/v1/org-edges", response_model=OrgEdgeOut, tags=["org-edges"])
    def create_org_edge(
        payload: OrgEdgeCreate,
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
        tenant: Annotated[TenantId, Depends(tenant_scope)],
    ) -> OrgEdgeOut:
        guard(request, ctx, principal, Permission.GRAPH_WRITE, tenant)
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
        stored = _store().put_org_edge(edge)
        ctx.audit.record(
            tenant_id=tenant.value,
            principal=principal,
            action="org_edge.create",
            category=AuditCategory.RESOURCE_CREATE,
            resource_type="org_edge",
            resource_id=stored.id.value,
            request_id=request_id_of(request),
        )
        return _org_edge_out(stored)

    @app.get("/api/v1/org-edges", tags=["org-edges"])
    def list_org_edges(
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
        tenant: Annotated[TenantId, Depends(tenant_scope)],
        page: Annotated[Page, Depends()],
        relation: Annotated[str | None, Query()] = None,
    ) -> Any:
        guard(request, ctx, principal, Permission.GRAPH_READ, tenant)
        parsed_relation = None
        if relation:
            try:
                parsed_relation = OrgRelation(relation.strip().lower())
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail=f"unknown org relation {relation!r}"
                ) from exc
        return paginate(
            [_org_edge_out(item) for item in _store().list_org_edges(tenant, relation=parsed_relation)],
            page,
        )

    @app.get("/api/v1/org-edges/{edge_id}", response_model=OrgEdgeOut, tags=["org-edges"])
    def get_org_edge(
        edge_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
        tenant: Annotated[TenantId, Depends(tenant_scope)],
    ) -> OrgEdgeOut:
        guard(request, ctx, principal, Permission.GRAPH_READ, tenant)
        return _org_edge_out(_store().get_org_edge(tenant, OrgEdgeId(tenant, edge_id)))

    @app.delete("/api/v1/org-edges/{edge_id}", status_code=204, tags=["org-edges"])
    def delete_org_edge(
        edge_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
        tenant: Annotated[TenantId, Depends(tenant_scope)],
    ) -> None:
        guard(request, ctx, principal, Permission.GRAPH_WRITE, tenant)
        _store().delete_org_edge(tenant, OrgEdgeId(tenant, edge_id))
        ctx.audit.record(
            tenant_id=tenant.value,
            principal=principal,
            action="org_edge.delete",
            category=AuditCategory.RESOURCE_DELETE,
            resource_type="org_edge",
            resource_id=edge_id,
            request_id=request_id_of(request),
        )

    # ----------------------------------------------------------- declarations
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
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
        tenant: Annotated[TenantId, Depends(tenant_scope)],
    ) -> dict[str, Any]:
        guard(request, ctx, principal, Permission.POPULATION_WRITE, tenant)
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
        result = _put_declaration(tenant, stored_pop, payload)
        ctx.audit.record(
            tenant_id=tenant.value,
            principal=principal,
            action="population_declaration.create",
            category=AuditCategory.RESOURCE_CREATE,
            resource_type="population",
            resource_id=stored_pop.id.value,
            request_id=request_id_of(request),
            details={"target_size": payload.target_size, "backend": payload.backend},
        )
        return result

    @app.put(
        "/api/v1/populations/{population_id}/declaration",
        tags=["population-declarations"],
    )
    def put_population_declaration(
        population_id: str,
        payload: PopulationDeclarationIn,
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
        tenant: Annotated[TenantId, Depends(tenant_scope)],
    ) -> dict[str, Any]:
        guard(request, ctx, principal, Permission.POPULATION_WRITE, tenant)
        population = _store().get_population(
            tenant, PopulationId(tenant, population_id)
        )
        result = _put_declaration(tenant, population, payload)
        ctx.audit.record(
            tenant_id=tenant.value,
            principal=principal,
            action="population_declaration.replace",
            category=AuditCategory.RESOURCE_MUTATE,
            resource_type="population",
            resource_id=population.id.value,
            request_id=request_id_of(request),
        )
        return result

    @app.get(
        "/api/v1/populations/{population_id}/declaration",
        tags=["population-declarations"],
    )
    def get_population_declaration(
        population_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
        tenant: Annotated[TenantId, Depends(tenant_scope)],
    ) -> dict[str, Any]:
        guard(request, ctx, principal, Permission.POPULATION_READ, tenant)
        declaration = _store().get_population_declaration(
            tenant, PopulationId(tenant, population_id)
        )
        return declaration.to_dict()

    _include_routers(app)
    return app


def mount_enterprise_api(host_app: FastAPI, *, context: EnterpriseContext | None = None) -> FastAPI:
    """Mount the enterprise app under the Playground backend (same origin).

    Requests to ``/api/v1/*``, ``/health`` and ``/ready`` of the mounted app are
    reachable at ``/enterprise/...`` on the host app; the Playground routes stay
    untouched.
    """
    enterprise = create_enterprise_app(context=context)
    host_app.mount("/enterprise", enterprise, name="enterprise")
    return enterprise


app = create_enterprise_app()
