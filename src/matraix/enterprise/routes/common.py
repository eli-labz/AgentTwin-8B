"""Shared request-scoped helpers for the ``/api/v1`` routers.

* :func:`get_context` — the :class:`EnterpriseContext` installed on the app.
* :func:`current_principal` — authenticated principal (401 when missing).
* :func:`tenant_scope` — resolves the tenant a request acts in: the
  principal's tenant, or ``X-Tenant-Id`` for platform admins; a mismatched
  header is 403. Tenant enforcement therefore happens before any handler body.
* :func:`guard` — permission check that also writes a denial audit row.
* :func:`paginate` — bare list by default (backward compatible), envelope
  ``{"items", "total", "limit", "offset", "next_offset"}`` when ``limit`` is set.
* :func:`error_body` — stable error object shared by every handler.
"""

from __future__ import annotations

from typing import Annotated, Any, Sequence, TypeVar

from fastapi import Depends, Header, HTTPException, Query, Request

from matraix.enterprise.audit import AuditCategory, AuditOutcome
from matraix.enterprise.auth.principal import Principal, require_tenant_scope
from matraix.enterprise.auth.roles import AuthenticationError, AuthorizationError, Permission
from matraix.enterprise.context import EnterpriseContext
from matraix.enterprise.errors import EnterpriseSchemaError
from matraix.enterprise.ids import TenantId

__all__ = [
    "IDEMPOTENCY_HEADER",
    "REQUEST_ID_HEADER",
    "TENANT_HEADER",
    "Page",
    "current_principal",
    "error_body",
    "get_context",
    "guard",
    "paginate",
    "request_id_of",
    "tenant_scope",
]

TENANT_HEADER = "X-Tenant-Id"
REQUEST_ID_HEADER = "X-Request-Id"
IDEMPOTENCY_HEADER = "Idempotency-Key"

T = TypeVar("T")


def error_body(code: str, message: str, request_id: str | None, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "detail": message,
        "error": {"code": code, "message": message, "request_id": request_id},
    }
    if details is not None:
        body["error"]["details"] = details
    return body


def request_id_of(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def get_context(request: Request) -> EnterpriseContext:
    ctx = getattr(request.app.state, "context", None)
    if ctx is None:
        raise HTTPException(status_code=500, detail="enterprise context is not configured")
    return ctx


def current_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail="authentication required",
        )
    return principal


def parse_tenant_id(raw: str) -> TenantId:
    try:
        return TenantId(raw)
    except EnterpriseSchemaError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def tenant_scope(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    x_tenant_id: Annotated[str | None, Header(alias=TENANT_HEADER)] = None,
) -> TenantId:
    """Tenant the request acts in. Header is optional for tenant-bound principals."""
    header_tenant = parse_tenant_id(x_tenant_id) if x_tenant_id and x_tenant_id.strip() else None
    if principal.platform_admin:
        if header_tenant is None:
            if principal.tenant_id:
                return TenantId(principal.tenant_id)
            raise HTTPException(status_code=400, detail=f"{TENANT_HEADER} header is required")
        return header_tenant
    if principal.tenant_id is None:
        raise HTTPException(status_code=403, detail="principal is not bound to a tenant")
    own = TenantId(principal.tenant_id)
    if header_tenant is not None and header_tenant != own:
        raise HTTPException(status_code=403, detail=f"{TENANT_HEADER} does not match the authenticated tenant")
    return own


def guard(
    request: Request,
    ctx: EnterpriseContext,
    principal: Principal,
    permission: Permission,
    tenant_id: TenantId | str | None = None,
) -> None:
    """Authorize or raise 403 after recording a denial audit event."""
    try:
        if tenant_id is not None:
            require_tenant_scope(principal, str(tenant_id.value if isinstance(tenant_id, TenantId) else tenant_id))
        if not principal.has(permission):
            raise AuthorizationError(f"principal {principal.subject!r} lacks permission {permission.value}")
    except (AuthorizationError, AuthenticationError) as exc:
        if tenant_id is not None:
            try:
                ctx.audit.record(
                    tenant_id=tenant_id.value if isinstance(tenant_id, TenantId) else str(tenant_id),
                    principal=principal,
                    action=f"deny:{permission.value}",
                    category=AuditCategory.DENIAL,
                    outcome=AuditOutcome.DENIED,
                    request_id=request_id_of(request),
                    details={"path": request.url.path, "reason": str(exc)},
                )
            except Exception:  # noqa: BLE001 - audit must not mask the denial
                pass
        raise HTTPException(status_code=403, detail=str(exc)) from exc


class Page:
    """Query parameters for optional pagination."""

    def __init__(
        self,
        limit: Annotated[int | None, Query(ge=1, le=1000)] = None,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> None:
        self.limit = limit
        self.offset = offset


def paginate(items: Sequence[T], page: Page | None) -> Any:
    if page is None or page.limit is None:
        return list(items)
    total = len(items)
    window = list(items[page.offset : page.offset + page.limit])
    next_offset = page.offset + page.limit if page.offset + page.limit < total else None
    return {
        "items": window,
        "total": total,
        "limit": page.limit,
        "offset": page.offset,
        "next_offset": next_offset,
    }
