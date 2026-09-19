"""Versioned AgentTwin Enterprise control-plane API (``/api/v1``).

Standalone FastAPI app — does not replace Playground. Tenant isolation is
enforced by ``X-Tenant-Id`` plus the repository contract. When
``MATRIX_ENTERPRISE_API_TOKEN`` is set, requests must send
``Authorization: Bearer <token>``. The token is read from the environment only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from matraix.enterprise.audit import new_audit_event
from matraix.enterprise.authz import authorize, permission_for
from matraix.enterprise.console import console_manifest
from matraix.enterprise.governance import GovernanceReview, ReviewKind, ReviewStatus
from matraix.enterprise.http_security import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    auth_is_required,
    configured_token,
    cookie_secure_defaults,
    csrf_cookie_defaults,
    csrf_is_required,
    decode_session_value,
    encode_session_value,
    is_public_path,
    new_csrf_token,
    resolve_cors_origins,
)
from matraix.enterprise.identity import (
    ANONYMOUS,
    Principal,
    parse_roles,
    service_principal,
    user_from_scim,
)
from matraix.enterprise.oidc import (
    decode_oidc_token,
    mint_dev_jwt,
    oidc_dev_secret,
    oidc_metadata,
    principal_from_claims,
)
from matraix.enterprise.rate_limit import check_rate_limit, is_sensitive_path

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
    ApprovalRequiredError,
    AuthorizationError,
    CrossTenantAccessError,
    EnterpriseSchemaError,
    EntityNotFoundError,
    PolicyDeniedError,
    WorkerNotAvailableError,
)
from matraix.enterprise.ids import (
    EntityKind,
    ExecutionId,
    ExperimentId,
    OrganizationId,
    OrgEdgeId,
    PersonaId,
    PopulationId,
    TenantId,
    UserId,
    new_id,
)
from matraix.enterprise.model_gateway import (
    ModelPolicy,
    ModelRequest,
    catalog_for_policy,
    complete_model,
    resolve_model_policy,
    route_model,
)
from matraix.enterprise.policy import (
    DataClassification,
    PolicyRequest,
    evaluate_policy,
)
from matraix.enterprise.population_builder import (
    PopulationSegment,
    build_population_declaration,
)
from matraix.enterprise.repositories import EnterpriseRepository
from matraix.enterprise.observability import evaluation_from_stored
from matraix.enterprise.reporting import render_report, report_from_runtime
from matraix.enterprise.runtime import EnterpriseRuntime, worker_catalog
from matraix.enterprise.store import (
    create_tenant_with_default_org,
    default_organization_for,
    open_enterprise_store,
)

TENANT_HEADER = "X-Tenant-Id"
_CONSOLE_HTML = Path(__file__).with_name("console.html")


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


class PolicyEvaluateIn(BaseModel):
    action: str
    resource: str
    persona_id: str | None = None
    environment: str | None = None
    tool: str | None = None
    model_provider: str | None = None
    data_classification: str = "INTERNAL"
    destination: str | None = None
    approved: bool = False
    attributes: dict[str, str] = Field(default_factory=dict)


class ModelRouteIn(BaseModel):
    action: str = "complete"
    resource: str = "model.complete"
    model_provider: str | None = None
    required_capability: str = "chat"
    residency: str | None = None
    task_complexity: str = "standard"
    data_classification: str = "INTERNAL"
    destination: str | None = None
    max_tokens: int = 256
    approved: bool = False
    messages: list[dict[str, str]] = Field(default_factory=list)
    persona_id: str | None = None


class ModelPolicyIn(BaseModel):
    allowed_providers: list[str] = Field(default_factory=list)
    denied_providers: list[str] = Field(default_factory=list)
    required_residency: str | None = None
    max_cost_score: float | None = None
    max_latency_ms: int | None = None
    allowed_capabilities: list[str] = Field(default_factory=list)
    allow_external: bool = False
    allow_live: bool = False
    default_decision: str = "SANDBOX_ONLY"
    denied_actions: list[str] = Field(default_factory=list)


class ExecutionSubmitIn(BaseModel):
    worker_kind: str = "local"
    approved: bool = False
    enable_world_state: bool = False
    model_provider: str | None = None
    experiment_id: str | None = None


class EvaluationSubmitIn(BaseModel):
    include_llm_judge: bool = False


class DevTokenIn(BaseModel):
    subject: str = "researcher"
    tenant_id: str | None = None
    roles: list[str] = Field(default_factory=lambda: ["researcher"])
    email: str | None = None


class SessionIn(BaseModel):
    subject: str | None = None
    tenant_id: str | None = None
    roles: list[str] = Field(default_factory=list)


class GovernanceReviewIn(BaseModel):
    kind: str
    title: str
    notes: str = ""
    status: str = "draft"
    sign_off: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    prefix = "bearer "
    if header.lower().startswith(prefix):
        return header[len(prefix) :].strip()
    return ""


def _resolve_principal(request: Request) -> Principal:
    if is_public_path(request.url.path):
        return ANONYMOUS
    bearer = _bearer_token(request)
    expected = configured_token()
    if bearer and expected and bearer == expected:
        return service_principal()
    if bearer and bearer.count(".") == 2 and oidc_dev_secret():
        try:
            return principal_from_claims(decode_oidc_token(bearer))
        except EnterpriseSchemaError:
            if auth_is_required():
                raise HTTPException(
                    status_code=401, detail="invalid or missing API token"
                ) from None
    session = request.cookies.get(SESSION_COOKIE)
    if session:
        parsed = decode_session_value(session)
        roles = parse_roles(
            [part for part in (parsed.get("roles") or "").split(",") if part]
        )
        tenant = None
        if parsed.get("tenant_id"):
            try:
                tenant = TenantId(parsed["tenant_id"])
            except EnterpriseSchemaError:
                tenant = None
        return Principal(
            subject=parsed.get("subject") or "session",
            source="session",
            tenant_id=tenant,
            roles=roles,
        )
    if auth_is_required():
        if expected is None:
            raise HTTPException(
                status_code=401,
                detail="API token is required but MATRIX_ENTERPRISE_API_TOKEN is unset",
            )
        raise HTTPException(status_code=401, detail="invalid or missing API token")
    return ANONYMOUS


def _require_token(request: Request) -> None:
    _resolve_principal(request)


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
            "simulation parameters, not psychological equivalents of humans. "
            "Phase 9 adds RBAC/OIDC patterns, append-only audit, CSRF, and rate limits."
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
            {
                "name": "policy",
                "description": "Policy gateway (SANDBOX_ONLY default; every execution is evaluated)",
            },
            {
                "name": "models",
                "description": "Provider-independent model gateway beside LiteLLM",
            },
            {
                "name": "runtime",
                "description": "Control / data / execution planes and worker catalog",
            },
            {
                "name": "telemetry",
                "description": "Traces, hierarchical metrics, evaluation (synthetic ≠ human research)",
            },
            {
                "name": "console",
                "description": "Enterprise console manifest (nav + wizard; synthetic ≠ human research)",
            },
            {
                "name": "reports",
                "description": "Executive reports and exports (synthetic ≠ human research)",
            },
            {
                "name": "auth",
                "description": "OIDC/SSO patterns, CSRF, and session cookies",
            },
            {
                "name": "audit",
                "description": "Append-only audit log (not telemetry)",
            },
            {
                "name": "governance",
                "description": "Ingestion / retention / model-provider reviews",
            },
            {
                "name": "identity",
                "description": "RBAC users and SCIM-shaped provisioning hooks",
            },
        ],
    )
    app.state.store = repository
    app.state.runtime = EnterpriseRuntime(repository)

    @app.exception_handler(CrossTenantAccessError)
    async def _cross_tenant(_request: Request, exc: CrossTenantAccessError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(EntityNotFoundError)
    async def _not_found(_request: Request, exc: EntityNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(EnterpriseSchemaError)
    async def _schema(_request: Request, exc: EnterpriseSchemaError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(PolicyDeniedError)
    async def _denied(_request: Request, exc: PolicyDeniedError) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={
                "detail": str(exc),
                "decision": exc.decision,
                "reasons": list(exc.reasons),
            },
        )

    @app.exception_handler(ApprovalRequiredError)
    async def _approval(_request: Request, exc: ApprovalRequiredError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc),
                "decision": exc.decision,
                "reasons": list(exc.reasons),
            },
        )

    @app.exception_handler(WorkerNotAvailableError)
    async def _worker(_request: Request, exc: WorkerNotAvailableError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "worker_kind": exc.kind},
        )

    @app.exception_handler(AuthorizationError)
    async def _authz(_request: Request, exc: AuthorizationError) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={"detail": str(exc), "permission": exc.permission},
        )

    @app.middleware("http")
    async def _security_middleware(request: Request, call_next):
        if request.method == "OPTIONS" or is_public_path(request.url.path):
            return await call_next(request)
        path = request.url.path
        client = request.client.host if request.client else "local"
        sensitive = is_sensitive_path(path) or request.method in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }
        if not check_rate_limit(f"{client}:{path}", sensitive=sensitive):
            return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})
        try:
            principal = _resolve_principal(request)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code, content={"detail": exc.detail}
            )
        request.state.principal = principal
        if csrf_is_required(
            request.method, has_session_cookie=bool(request.cookies.get(SESSION_COOKIE))
        ):
            cookie = request.cookies.get(CSRF_COOKIE) or ""
            header = request.headers.get(CSRF_HEADER) or ""
            if not cookie or not header or cookie != header:
                return JSONResponse(
                    status_code=403, content={"detail": "CSRF token missing or invalid"}
                )
        needed = permission_for(request.method, path)
        if needed is not None and not principal.anonymous:
            header_tenant = request.headers.get("x-tenant-id")
            tenant = None
            if header_tenant:
                try:
                    tenant = TenantId(header_tenant)
                except EnterpriseSchemaError:
                    tenant = None
            try:
                authorize(principal, needed, tenant_id=tenant, resource=path)
            except AuthorizationError as exc:
                return JSONResponse(
                    status_code=403,
                    content={"detail": str(exc), "permission": exc.permission},
                )
        response = await call_next(request)
        raw_tenant = request.headers.get("x-tenant-id")
        if raw_tenant and path.startswith("/api/v1"):
            try:
                tenant = TenantId(raw_tenant)
                _store().append_audit(
                    new_audit_event(
                        actor=principal.subject,
                        action=f"{request.method} {path}",
                        resource=path,
                        result=str(response.status_code),
                        tenant_id=tenant,
                        ip=client,
                        details={"source": principal.source},
                    )
                )
            except Exception:
                pass
        return response

    cors_origins = resolve_cors_origins()
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def _store() -> EnterpriseRepository:
        return app.state.store

    def _runtime() -> EnterpriseRuntime:
        return app.state.runtime

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/console", include_in_schema=False)
    @app.get("/console/", include_in_schema=False)
    def enterprise_console() -> FileResponse:
        return FileResponse(_CONSOLE_HTML, media_type="text/html")

    @app.get("/api/v1/console/manifest", tags=["console"])
    def get_console_manifest() -> dict[str, Any]:
        return console_manifest()

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

    def _model_policy_for(tenant: TenantId) -> ModelPolicy:
        return resolve_model_policy(tenant, _store().get_model_policy(tenant))

    def _model_request(tenant: TenantId, payload: ModelRouteIn) -> ModelRequest:
        persona = PersonaId(tenant, payload.persona_id) if payload.persona_id else None
        try:
            classification = DataClassification(payload.data_classification)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"unknown data classification {payload.data_classification!r}",
            ) from exc
        try:
            return ModelRequest(
                tenant_id=tenant,
                messages=tuple(payload.messages),
                action=payload.action,
                resource=payload.resource,
                persona_id=persona,
                model_provider=payload.model_provider,
                required_capability=payload.required_capability,
                residency=payload.residency,
                task_complexity=payload.task_complexity,
                data_classification=classification,
                destination=payload.destination,
                max_tokens=payload.max_tokens,
                approved=payload.approved,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/policy/evaluate", tags=["policy"])
    def evaluate_execution_policy(
        payload: PolicyEvaluateIn,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        try:
            classification = DataClassification(payload.data_classification)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"unknown data classification {payload.data_classification!r}",
            ) from exc
        persona = PersonaId(tenant, payload.persona_id) if payload.persona_id else None
        request = PolicyRequest(
            tenant_id=tenant,
            action=payload.action,
            resource=payload.resource,
            persona_id=persona,
            environment=payload.environment,
            tool=payload.tool,
            model_provider=payload.model_provider,
            data_classification=classification,
            destination=payload.destination,
            approved=payload.approved,
            attributes=tuple(payload.attributes.items()),
        )
        return evaluate_policy(request, _model_policy_for(tenant)).to_dict()

    @app.get("/api/v1/model-policy", tags=["models"])
    def get_model_policy(
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        return _store().get_model_policy(tenant).to_dict()

    @app.put("/api/v1/model-policy", tags=["models"])
    def put_model_policy(
        payload: ModelPolicyIn,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        try:
            policy = ModelPolicy(
                tenant_id=tenant,
                allowed_providers=tuple(payload.allowed_providers),
                denied_providers=tuple(payload.denied_providers),
                required_residency=payload.required_residency,
                max_cost_score=payload.max_cost_score,
                max_latency_ms=payload.max_latency_ms,
                allowed_capabilities=tuple(payload.allowed_capabilities),
                allow_external=payload.allow_external,
                allow_live=payload.allow_live,
                default_decision=payload.default_decision,
                denied_actions=tuple(payload.denied_actions),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _store().put_model_policy(policy).to_dict()

    @app.get("/api/v1/models/catalog", tags=["models"])
    def list_model_catalog(
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> list[dict[str, Any]]:
        tenant = _require_tenant_header(x_tenant_id)
        return catalog_for_policy(_model_policy_for(tenant))

    @app.post("/api/v1/models/route", tags=["models"])
    def route_model_request(
        payload: ModelRouteIn,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        return route_model(_model_request(tenant, payload), _model_policy_for(tenant)).to_dict()

    @app.post("/api/v1/models/complete", tags=["models"])
    def complete_model_request(
        payload: ModelRouteIn,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        return complete_model(
            _model_request(tenant, payload), _model_policy_for(tenant)
        ).to_dict()

    @app.get("/api/v1/workers", tags=["runtime"])
    def list_workers() -> list[dict[str, Any]]:
        return worker_catalog()

    @app.post("/api/v1/experiments/{experiment_id}/execute", tags=["runtime"])
    def execute_experiment(
        experiment_id: str,
        payload: ExecutionSubmitIn | None = None,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        body = payload or ExecutionSubmitIn()
        return _runtime().execute_experiment(
            tenant,
            ExperimentId(tenant, experiment_id),
            worker_kind=body.worker_kind,
            approved=body.approved,
            enable_world_state=body.enable_world_state,
            model_provider=body.model_provider,
        ).to_dict()

    @app.post("/api/v1/executions", tags=["runtime"])
    def create_execution(
        payload: ExecutionSubmitIn,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        if not payload.experiment_id:
            raise HTTPException(status_code=400, detail="experiment_id is required")
        return _runtime().execute_experiment(
            tenant,
            ExperimentId(tenant, payload.experiment_id),
            worker_kind=payload.worker_kind,
            approved=payload.approved,
            enable_world_state=payload.enable_world_state,
            model_provider=payload.model_provider,
        ).to_dict()

    @app.get("/api/v1/executions", tags=["runtime"])
    def list_executions(
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> list[dict[str, Any]]:
        tenant = _require_tenant_header(x_tenant_id)
        return [item.to_dict() for item in _runtime().execution.list(tenant)]

    @app.get("/api/v1/executions/{execution_id}", tags=["runtime"])
    def get_execution(
        execution_id: str,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        return _runtime().execution.get(
            tenant, ExecutionId(tenant, execution_id)
        ).to_dict()

    @app.get("/api/v1/executions/{execution_id}/artifacts", tags=["runtime"])
    def list_execution_artifacts(
        execution_id: str,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> list[dict[str, Any]]:
        tenant = _require_tenant_header(x_tenant_id)
        resolved = ExecutionId(tenant, execution_id)
        _runtime().execution.get(tenant, resolved)
        return [
            item.to_dict()
            for item in _runtime().data.list_artifacts(tenant, execution_id=resolved)
        ]

    @app.get("/api/v1/events", tags=["runtime"])
    def list_runtime_events(
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
        execution_id: Annotated[str | None, Query()] = None,
    ) -> list[dict[str, Any]]:
        tenant = _require_tenant_header(x_tenant_id)
        resolved = ExecutionId(tenant, execution_id) if execution_id else None
        return [
            item.to_dict()
            for item in _runtime().data.list_events(tenant, execution_id=resolved)
        ]

    def _require_execution(tenant: TenantId, execution_id: str):
        resolved = ExecutionId(tenant, execution_id)
        record = _runtime().execution.get(tenant, resolved)
        return resolved, record

    @app.get("/api/v1/executions/{execution_id}/trace", tags=["telemetry"])
    def get_execution_trace(
        execution_id: str,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        resolved, _record = _require_execution(tenant, execution_id)
        return _runtime().data.artifact_by_kind(tenant, resolved, "trace").content

    @app.get("/api/v1/executions/{execution_id}/metrics", tags=["telemetry"])
    def get_execution_metrics(
        execution_id: str,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        resolved, _record = _require_execution(tenant, execution_id)
        return _runtime().data.artifact_by_kind(tenant, resolved, "metrics").content

    @app.get("/api/v1/executions/{execution_id}/evaluation", tags=["telemetry"])
    def get_execution_evaluation(
        execution_id: str,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        resolved, _record = _require_execution(tenant, execution_id)
        return _runtime().data.artifact_by_kind(tenant, resolved, "evaluation").content

    @app.post("/api/v1/executions/{execution_id}/evaluate", tags=["telemetry"])
    def reevaluate_execution(
        execution_id: str,
        payload: EvaluationSubmitIn | None = None,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        resolved, record = _require_execution(tenant, execution_id)
        artifacts = [
            item.to_dict()
            for item in _runtime().data.list_artifacts(tenant, execution_id=resolved)
        ]
        body = payload or EvaluationSubmitIn()
        bundle = evaluation_from_stored(
            tenant_id=tenant,
            execution_id=resolved,
            experiment_id=record.experiment_id,
            result=record.result,
            artifacts=artifacts,
            status=record.status.value,
            reasons=record.reasons,
            seed=record.result.get("seed"),
            include_llm_judge=body.include_llm_judge,
        )
        return bundle.to_dict()

    @app.get("/api/v1/failures", tags=["telemetry"])
    def list_failures(
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
        execution_id: Annotated[str | None, Query()] = None,
    ) -> list[dict[str, Any]]:
        tenant = _require_tenant_header(x_tenant_id)
        resolved = ExecutionId(tenant, execution_id) if execution_id else None
        items = _runtime().data.list_artifacts(tenant, execution_id=resolved)
        return [item.content for item in items if item.kind == "failure"]

    def _report_response(report, fmt: str):
        try:
            body, media = render_report(report, fmt)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if media.startswith("text/html"):
            return HTMLResponse(body)
        if media.startswith("text/csv"):
            return Response(content=body, media_type=media)
        return JSONResponse(content=report.to_dict())

    @app.get("/api/v1/executions/{execution_id}/report", tags=["reports"])
    def get_execution_report(
        execution_id: str,
        fmt: Annotated[str, Query(alias="format")] = "json",
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ):
        tenant = _require_tenant_header(x_tenant_id)
        resolved, _record = _require_execution(tenant, execution_id)
        return _report_response(
            report_from_runtime(
                _runtime(), tenant_id=tenant, execution_id=resolved
            ),
            fmt,
        )

    @app.get("/api/v1/experiments/{experiment_id}/report", tags=["reports"])
    def get_experiment_report(
        experiment_id: str,
        fmt: Annotated[str, Query(alias="format")] = "json",
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ):
        tenant = _require_tenant_header(x_tenant_id)
        return _report_response(
            report_from_runtime(
                _runtime(),
                tenant_id=tenant,
                experiment_id=ExperimentId(tenant, experiment_id),
            ),
            fmt,
        )

    @app.get("/api/v1/auth/oidc", tags=["auth"])
    def get_oidc_metadata() -> dict[str, Any]:
        return oidc_metadata()

    @app.get("/api/v1/auth/csrf", tags=["auth"])
    def get_csrf_token() -> JSONResponse:
        token = new_csrf_token()
        response = JSONResponse({"csrf_token": token})
        response.set_cookie(CSRF_COOKIE, token, **csrf_cookie_defaults())
        return response

    @app.get("/api/v1/auth/me", tags=["auth"])
    def get_auth_me(request: Request) -> dict[str, Any]:
        principal = getattr(request.state, "principal", None) or _resolve_principal(
            request
        )
        return principal.to_dict()

    @app.post("/api/v1/auth/dev-token", tags=["auth"])
    def create_dev_token(payload: DevTokenIn) -> dict[str, Any]:
        token = mint_dev_jwt(
            subject=payload.subject,
            tenant_id=payload.tenant_id,
            roles=payload.roles,
            email=payload.email,
        )
        return {"token": token, "token_type": "Bearer"}

    @app.post("/api/v1/auth/session", tags=["auth"])
    def create_session(request: Request, payload: SessionIn | None = None) -> JSONResponse:
        principal = getattr(request.state, "principal", None) or _resolve_principal(
            request
        )
        body = payload or SessionIn()
        subject = body.subject or principal.subject
        tenant = body.tenant_id or (
            principal.tenant_id.value if principal.tenant_id else ""
        )
        roles = ",".join(body.roles or [role.value for role in principal.roles])
        csrf = new_csrf_token()
        response = JSONResponse(
            {
                "subject": subject,
                "tenant_id": tenant or None,
                "csrf_token": csrf,
            }
        )
        response.set_cookie(
            SESSION_COOKIE,
            encode_session_value(subject=subject, tenant_id=tenant, roles=roles),
            **cookie_secure_defaults(),
        )
        response.set_cookie(CSRF_COOKIE, csrf, **csrf_cookie_defaults())
        return response

    @app.post("/api/v1/auth/logout", tags=["auth"])
    def logout_session() -> JSONResponse:
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return response

    @app.get("/api/v1/audit", tags=["audit"])
    def list_audit(
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> list[dict[str, Any]]:
        tenant = _require_tenant_header(x_tenant_id)
        return [item.to_dict() for item in _store().list_audit(tenant)]

    @app.get("/api/v1/audit/export", tags=["audit"])
    def export_audit(
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        return {
            "schema_version": "EnterpriseAuditExport.v1",
            "tenant_id": tenant.value,
            "events": _store().export_audit(tenant),
        }

    @app.post("/api/v1/governance/reviews", tags=["governance"])
    def create_governance_review(
        payload: GovernanceReviewIn,
        request: Request,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        principal = getattr(request.state, "principal", None) or ANONYMOUS
        review = GovernanceReview(
            tenant_id=tenant,
            kind=ReviewKind(payload.kind),
            title=payload.title,
            notes=payload.notes,
            status=ReviewStatus(payload.status),
            sign_off=payload.sign_off,
            reviewer=principal.subject,
            details=payload.details,
        )
        return _store().put_governance_review(review).to_dict()

    @app.get("/api/v1/governance/reviews", tags=["governance"])
    def list_governance_reviews(
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> list[dict[str, Any]]:
        tenant = _require_tenant_header(x_tenant_id)
        return [item.to_dict() for item in _store().list_governance_reviews(tenant)]

    @app.get("/api/v1/users", tags=["identity"])
    def list_users(
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> list[dict[str, Any]]:
        tenant = _require_tenant_header(x_tenant_id)
        return [item.to_dict() for item in _store().list_users(tenant)]

    @app.post("/api/v1/scim/Users", tags=["identity"])
    def scim_create_user(
        payload: dict[str, Any],
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        user = user_from_scim(tenant, payload)
        return _store().put_user(user).to_scim()

    @app.get("/api/v1/scim/Users", tags=["identity"])
    def scim_list_users(
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        users = _store().list_users(tenant)
        return {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": len(users),
            "Resources": [item.to_scim() for item in users],
        }

    @app.get("/api/v1/scim/Users/{user_id}", tags=["identity"])
    def scim_get_user(
        user_id: str,
        x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
    ) -> dict[str, Any]:
        tenant = _require_tenant_header(x_tenant_id)
        return _store().get_user(tenant, UserId(tenant, user_id)).to_scim()

    return app


app = create_enterprise_app()
