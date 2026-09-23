"""Population engine routes: versions, materialization, quality, cohorts.

Materialization is a mutating, potentially long operation, so it accepts an
``Idempotency-Key`` header: replaying the same key returns the first response
instead of building a second population version.

Composition responses report **aggregate** subgroup counts and bounded
dimension summaries. They never stream full 1,290-dimension persona records, so
a dashboard cannot become an unnecessary disclosure of per-persona attributes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from matraix.enterprise.audit import AuditCategory, AuditOutcome
from matraix.enterprise.auth.principal import Principal
from matraix.enterprise.auth.roles import Permission
from matraix.enterprise.context import EnterpriseContext
from matraix.enterprise.domain import Cohort, PopulationVersion, PopulationVersionStatus
from matraix.enterprise.ids import EntityKind, PopulationId, TenantId
from matraix.enterprise.population import CohortSelection, build_cohort, materialize_population
from matraix.enterprise.records import RecordQuery
from matraix.enterprise.routes.common import (
    IDEMPOTENCY_HEADER,
    Page,
    current_principal,
    get_context,
    guard,
    paginate,
    request_id_of,
    tenant_scope,
)

router = APIRouter(prefix="/api/v1", tags=["populations"])


class MaterializeIn(BaseModel):
    seed: int = Field(42, description="Deterministic seed; same seed reproduces the population byte for byte.")
    evidence_pool: str | None = Field(
        default=None,
        description="Persona pool backing the treiver / evidence-grounded backend.",
    )
    segment_weights: dict[str, float] = Field(default_factory=dict)
    allow_replacement: bool = Field(
        default=False,
        description="Permit oversampling with replacement when a segment's pool is smaller than its quota.",
    )
    key_dimensions: list[str] = Field(
        default_factory=list,
        description="Dimensions to summarize per persona for filtering and composition views.",
    )


class PopulationVersionOut(BaseModel):
    id: str
    tenant_id: str
    population_id: str
    version_number: int
    status: str
    seed: int
    sampling_method: str
    schema_version: str
    generator_version: str
    model_version: str | None
    privacy_mode: str
    target_size: int
    realized_size: int
    manifest_hash: str | None
    artifact_root: str | None
    source_datasets: list[dict[str, Any]]
    representativeness_claim: str
    validation_findings: list[dict[str, Any]]
    version: int
    created_at: Any
    created_by: str | None


class CohortIn(BaseModel):
    name: str
    seed: int = 42
    size: int | None = None
    filters: dict[str, list[str]] = Field(default_factory=dict)
    stratify_by: list[str] = Field(default_factory=list)
    segment_shares: dict[str, float] = Field(default_factory=dict)


class CohortOut(BaseModel):
    id: str
    tenant_id: str
    name: str
    population_version_id: str
    seed: int
    size: int
    selection: dict[str, Any]
    content_hash: str
    persona_refs: list[str]
    created_at: Any


def _version_out(version: PopulationVersion) -> PopulationVersionOut:
    return PopulationVersionOut(
        id=version.id,
        tenant_id=version.tenant_id,
        population_id=version.population_id,
        version_number=version.version_number,
        status=version.status,
        seed=version.seed,
        sampling_method=version.sampling_method,
        schema_version=version.schema_version,
        generator_version=version.generator_version,
        model_version=version.model_version,
        privacy_mode=version.privacy_mode,
        target_size=version.target_size,
        realized_size=version.realized_size,
        manifest_hash=version.manifest_hash,
        artifact_root=version.artifact_root,
        source_datasets=version.source_datasets,
        representativeness_claim=version.representativeness_claim,
        validation_findings=version.validation_findings,
        version=version.version,
        created_at=version.created_at,
        created_by=version.created_by,
    )


def _cohort_out(cohort: Cohort) -> CohortOut:
    return CohortOut(
        id=cohort.id,
        tenant_id=cohort.tenant_id,
        name=cohort.name,
        population_version_id=cohort.population_version_id,
        seed=cohort.seed,
        size=cohort.size,
        selection=cohort.selection,
        content_hash=cohort.content_hash,
        persona_refs=cohort.persona_refs,
        created_at=cohort.created_at,
    )


def _get_version(ctx: EnterpriseContext, tenant: TenantId, version_id: str) -> PopulationVersion:
    return ctx.store.get_record(tenant.value, EntityKind.POPULATION_VERSION, version_id, PopulationVersion)


@router.post("/populations/{population_id}/versions", response_model=PopulationVersionOut)
def materialize_version(
    population_id: str,
    payload: MaterializeIn,
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
    idempotency_key: Annotated[str | None, Header(alias=IDEMPOTENCY_HEADER)] = None,
) -> Any:
    """Materialize the population's declaration into an immutable version."""
    guard(request, ctx, principal, Permission.POPULATION_MATERIALIZE, tenant)
    scope = f"materialize:{population_id}"
    if idempotency_key:
        cached = ctx.store.get_idempotent(tenant.value, scope, idempotency_key)
        if cached is not None:
            return cached

    population = ctx.store.get_population(tenant, PopulationId(tenant, population_id))
    declaration = ctx.store.get_population_declaration(tenant, population.id)
    try:
        result = materialize_population(
            ctx.store,
            declaration=declaration,
            seed=payload.seed,
            artifact_root=ctx.settings.artifact_root,
            repo_root=ctx.settings.repo_root,
            created_by=principal.subject,
            evidence_pool=payload.evidence_pool or "persona/datasets/matraix-persona-dev-sample",
            segment_weights=payload.segment_weights or None,
            allow_replacement=payload.allow_replacement,
            key_dimensions=payload.key_dimensions,
        )
    except Exception as exc:
        # A failed privileged operation must leave a trail, not only a successful one.
        ctx.audit.record(
            tenant_id=tenant.value,
            principal=principal,
            action="population_version.materialize",
            category=AuditCategory.RESOURCE_CREATE,
            outcome=AuditOutcome.FAILURE,
            resource_type="population",
            resource_id=population_id,
            request_id=request_id_of(request),
            details={"seed": payload.seed, "error_class": exc.__class__.__name__, "error": str(exc)},
        )
        raise
    ctx.audit.record(
        tenant_id=tenant.value,
        principal=principal,
        action="population_version.materialize",
        category=AuditCategory.RESOURCE_CREATE,
        resource_type="population_version",
        resource_id=result.version.id,
        request_id=request_id_of(request),
        details={
            "population_id": population_id,
            "seed": payload.seed,
            "realized_size": result.version.realized_size,
            "manifest_hash": result.version.manifest_hash,
            "sampling_method": result.version.sampling_method,
        },
    )
    body = _version_out(result.version)
    if idempotency_key:
        ctx.store.put_idempotent(tenant.value, scope, idempotency_key, body.model_dump(mode="json"))
    return body


@router.get("/populations/{population_id}/versions")
def list_versions(
    population_id: str,
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
    page: Annotated[Page, Depends()],
) -> Any:
    guard(request, ctx, principal, Permission.POPULATION_READ, tenant)
    ctx.store.get_population(tenant, PopulationId(tenant, population_id))
    versions = ctx.store.list_records(
        tenant.value,
        EntityKind.POPULATION_VERSION,
        PopulationVersion,
        RecordQuery(parent_id=population_id),
    )
    versions.sort(key=lambda item: item.version_number)
    return paginate([_version_out(item) for item in versions], page)


@router.get("/population-versions/{version_id}", response_model=PopulationVersionOut)
def get_version(
    version_id: str,
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
) -> PopulationVersionOut:
    guard(request, ctx, principal, Permission.POPULATION_READ, tenant)
    return _version_out(_get_version(ctx, tenant, version_id))


@router.get("/population-versions/{version_id}/quality")
def get_version_quality(
    version_id: str,
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
) -> dict[str, Any]:
    guard(request, ctx, principal, Permission.POPULATION_READ, tenant)
    version = _get_version(ctx, tenant, version_id)
    return {
        "population_version_id": version.id,
        "status": version.status,
        "validity": "SIMULATION_ONLY",
        "representativeness_claim": version.representativeness_claim,
        "quality": version.quality,
        "findings": version.validation_findings,
    }


@router.get("/population-versions/{version_id}/composition")
def get_version_composition(
    version_id: str,
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
) -> dict[str, Any]:
    """Aggregate composition for dashboards: counts only, no full persona records."""
    guard(request, ctx, principal, Permission.POPULATION_READ, tenant)
    version = _get_version(ctx, tenant, version_id)
    quality = version.quality or {}
    return {
        "population_version_id": version.id,
        "realized_size": version.realized_size,
        "target_size": version.target_size,
        "segment_counts": quality.get("segment_counts", {}),
        "subgroup_counts": quality.get("subgroup_counts", {}),
        "rare_groups": quality.get("rare_groups", []),
        "source_counts": quality.get("source_counts", {}),
        "distribution_deviation": quality.get("distribution_deviation", {}),
        "effective_sample_size": quality.get("effective_sample_size"),
        "weights_vary": quality.get("weights_vary", False),
        "validity": "SIMULATION_ONLY",
    }


@router.get("/population-versions/{version_id}/personas")
def list_version_personas(
    version_id: str,
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    guard(request, ctx, principal, Permission.POPULATION_READ, tenant)
    version = _get_version(ctx, tenant, version_id)
    rows = ctx.store.list_persona_snapshots(tenant.value, version.id, limit=limit, offset=offset)
    total = ctx.store.count_persona_snapshots(tenant.value, version.id)
    return {
        "items": [
            {
                "seq": row.seq,
                "persona_ref": row.persona_ref,
                "path": row.path,
                "content_hash": row.content_hash,
                "segment": row.segment,
                "source": row.source,
                "weight": row.weight,
                "dimensions_summary": row.dimensions_summary,
            }
            for row in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < total else None,
    }


@router.post("/population-versions/{version_id}/freeze", response_model=PopulationVersionOut)
def freeze_version(
    version_id: str,
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
) -> PopulationVersionOut:
    """Mark a ready version frozen. Frozen versions never change again."""
    guard(request, ctx, principal, Permission.POPULATION_WRITE, tenant)
    version = _get_version(ctx, tenant, version_id)
    if version.status == PopulationVersionStatus.FROZEN.value:
        return _version_out(version)
    if version.status != PopulationVersionStatus.READY.value:
        raise HTTPException(status_code=409, detail=f"population version is {version.status}; only READY can be frozen")
    frozen = ctx.store.put_record(version.with_update(status=PopulationVersionStatus.FROZEN.value))
    ctx.audit.record(
        tenant_id=tenant.value,
        principal=principal,
        action="population_version.freeze",
        category=AuditCategory.RESOURCE_MUTATE,
        resource_type="population_version",
        resource_id=version.id,
        request_id=request_id_of(request),
    )
    return _version_out(frozen)


@router.post("/population-versions/{version_id}/cohorts", response_model=CohortOut)
def create_cohort(
    version_id: str,
    payload: CohortIn,
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
) -> CohortOut:
    guard(request, ctx, principal, Permission.POPULATION_WRITE, tenant)
    version = _get_version(ctx, tenant, version_id)
    cohort = build_cohort(
        ctx.store,
        version=version,
        name=payload.name,
        seed=payload.seed,
        selection=CohortSelection(
            size=payload.size,
            filters=payload.filters,
            stratify_by=payload.stratify_by,
            segment_shares=payload.segment_shares,
        ),
        repo_root=Path(ctx.settings.repo_root),
        created_by=principal.subject,
    )
    ctx.audit.record(
        tenant_id=tenant.value,
        principal=principal,
        action="cohort.create",
        category=AuditCategory.RESOURCE_CREATE,
        resource_type="cohort",
        resource_id=cohort.id,
        request_id=request_id_of(request),
        details={"population_version_id": version.id, "seed": payload.seed, "size": cohort.size},
    )
    return _cohort_out(cohort)


@router.get("/cohorts")
def list_cohorts(
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
    page: Annotated[Page, Depends()],
    population_version_id: Annotated[str | None, Query()] = None,
) -> Any:
    guard(request, ctx, principal, Permission.POPULATION_READ, tenant)
    query = RecordQuery(parent_id=population_version_id) if population_version_id else RecordQuery()
    cohorts = ctx.store.list_records(tenant.value, EntityKind.COHORT, Cohort, query)
    return paginate([_cohort_out(item) for item in cohorts], page)


@router.get("/cohorts/{cohort_id}", response_model=CohortOut)
def get_cohort(
    cohort_id: str,
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
) -> CohortOut:
    guard(request, ctx, principal, Permission.POPULATION_READ, tenant)
    return _cohort_out(ctx.store.get_record(tenant.value, EntityKind.COHORT, cohort_id, Cohort))
