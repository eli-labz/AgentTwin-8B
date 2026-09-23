"""Audit routes: read the append-only stream and verify its hash chain."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from matraix.enterprise.auth.principal import Principal
from matraix.enterprise.auth.roles import Permission
from matraix.enterprise.context import EnterpriseContext
from matraix.enterprise.ids import TenantId
from matraix.enterprise.routes.common import current_principal, get_context, guard, tenant_scope

router = APIRouter(prefix="/api/v1", tags=["audit"])


@router.get("/audit-events")
def list_audit_events(
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    category: Annotated[str | None, Query()] = None,
    action: Annotated[str | None, Query()] = None,
    resource_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    guard(request, ctx, principal, Permission.AUDIT_READ, tenant)
    rows = ctx.audit.list(
        tenant.value, limit=limit, offset=offset, category=category, action=action, resource_id=resource_id
    )
    return {
        "items": [row.to_dict() for row in rows],
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if len(rows) == limit else None,
    }


@router.get("/audit-events/verify")
def verify_audit_chain(
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
) -> dict[str, Any]:
    guard(request, ctx, principal, Permission.AUDIT_READ, tenant)
    return ctx.audit.verify_chain(tenant.value)
