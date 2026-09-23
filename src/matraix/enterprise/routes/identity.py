"""Identity routes: who am I, users, service accounts, role bindings.

Service-account creation returns the raw token exactly once; only its hash is
persisted. All mutations are audited.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from matraix.enterprise.audit import AuditCategory
from matraix.enterprise.auth.principal import Principal
from matraix.enterprise.auth.roles import Permission, role_from_string
from matraix.enterprise.auth.tokens import generate_token, hash_token, token_prefix
from matraix.enterprise.context import EnterpriseContext
from matraix.enterprise.domain import PrincipalKind, RoleBinding, RoleName, ServiceAccount, User
from matraix.enterprise.errors import EnterpriseError
from matraix.enterprise.ids import EntityKind, TenantId
from matraix.enterprise.records import RecordQuery
from matraix.enterprise.routes.common import (
    Page,
    current_principal,
    get_context,
    guard,
    paginate,
    request_id_of,
    tenant_scope,
)

router = APIRouter(prefix="/api/v1", tags=["identities"])


class UserCreate(BaseModel):
    subject: str
    display_name: str | None = None
    email: str | None = None
    roles: list[str] = Field(default_factory=lambda: ["viewer"])
    identity_provider: str = "local"
    external_id: str | None = None
    organization_id: str | None = None


class UserOut(BaseModel):
    id: str
    tenant_id: str
    organization_id: str | None
    subject: str
    display_name: str | None
    email: str | None
    roles: list[str]
    identity_provider: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class ServiceAccountCreate(BaseModel):
    name: str
    roles: list[str] = Field(default_factory=lambda: ["viewer"])
    expires_at: datetime | None = None
    organization_id: str | None = None


class ServiceAccountOut(BaseModel):
    id: str
    tenant_id: str
    name: str
    roles: list[str]
    token_prefix: str
    status: str
    expires_at: datetime | None
    version: int
    created_at: datetime


class ServiceAccountCreated(ServiceAccountOut):
    token: str = Field(description="Shown once. Store it in your secret manager.")


class RoleBindingCreate(BaseModel):
    principal_id: str
    principal_kind: str = "user"
    role: str
    scope_kind: str = "tenant"
    scope_id: str | None = None


class RoleBindingOut(BaseModel):
    id: str
    tenant_id: str
    principal_id: str
    principal_kind: str
    role: str
    scope_kind: str
    scope_id: str | None
    created_at: datetime


def _roles(values: list[str]) -> list[RoleName]:
    try:
        return [role_from_string(item) for item in values]
    except EnterpriseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        tenant_id=user.tenant_id,
        organization_id=user.organization_id,
        subject=user.subject,
        display_name=user.display_name,
        email=user.email,
        roles=[role.value for role in user.roles],
        identity_provider=user.identity_provider,
        status=user.status,
        version=user.version,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _sa_out(account: ServiceAccount) -> dict[str, Any]:
    return {
        "id": account.id,
        "tenant_id": account.tenant_id,
        "name": account.name,
        "roles": [role.value for role in account.roles],
        "token_prefix": account.token_prefix,
        "status": account.status,
        "expires_at": account.expires_at,
        "version": account.version,
        "created_at": account.created_at,
    }


@router.get("/whoami")
def whoami(principal: Annotated[Principal, Depends(current_principal)]) -> dict[str, Any]:
    return principal.to_dict()


@router.post("/users", response_model=UserOut)
def create_user(
    payload: UserCreate,
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
) -> UserOut:
    guard(request, ctx, principal, Permission.IDENTITY_WRITE, tenant)
    ctx.store.get_tenant(tenant)
    user = User.create(
        tenant_id=tenant.value,
        organization_id=payload.organization_id,
        created_by=principal.subject,
        subject=payload.subject,
        display_name=payload.display_name,
        email=payload.email,
        roles=_roles(payload.roles),
        identity_provider=payload.identity_provider,
        external_id=payload.external_id,
    )
    ctx.store.put_record(user)
    ctx.audit.record(
        tenant_id=tenant.value,
        principal=principal,
        action="user.create",
        category=AuditCategory.ADMIN,
        resource_type="user",
        resource_id=user.id,
        request_id=request_id_of(request),
        details={"subject": user.subject, "roles": payload.roles},
    )
    return _user_out(user)


@router.get("/users")
def list_users(
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
    page: Annotated[Page, Depends()],
) -> Any:
    guard(request, ctx, principal, Permission.IDENTITY_READ, tenant)
    users = ctx.store.list_records(tenant.value, EntityKind.USER, User, RecordQuery())
    return paginate([_user_out(item) for item in users], page)


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(
    user_id: str,
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
) -> UserOut:
    guard(request, ctx, principal, Permission.IDENTITY_READ, tenant)
    return _user_out(ctx.store.get_record(tenant.value, EntityKind.USER, user_id, User))


@router.post("/service-accounts", response_model=ServiceAccountCreated)
def create_service_account(
    payload: ServiceAccountCreate,
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
) -> ServiceAccountCreated:
    guard(request, ctx, principal, Permission.IDENTITY_WRITE, tenant)
    ctx.store.get_tenant(tenant)
    raw = generate_token()
    account = ServiceAccount.create(
        tenant_id=tenant.value,
        organization_id=payload.organization_id,
        created_by=principal.subject,
        name=payload.name,
        roles=_roles(payload.roles),
        token_hash=hash_token(raw),
        token_prefix=token_prefix(raw),
        expires_at=payload.expires_at,
    )
    ctx.store.put_record(account)
    ctx.audit.record(
        tenant_id=tenant.value,
        principal=principal,
        action="service_account.create",
        category=AuditCategory.ADMIN,
        resource_type="service_account",
        resource_id=account.id,
        request_id=request_id_of(request),
        details={"name": account.name, "roles": payload.roles, "token_prefix": account.token_prefix},
    )
    return ServiceAccountCreated(**_sa_out(account), token=raw)


@router.get("/service-accounts")
def list_service_accounts(
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
    page: Annotated[Page, Depends()],
) -> Any:
    guard(request, ctx, principal, Permission.IDENTITY_READ, tenant)
    accounts = ctx.store.list_records(tenant.value, EntityKind.SERVICE_ACCOUNT, ServiceAccount, RecordQuery())
    return paginate([ServiceAccountOut(**_sa_out(item)) for item in accounts], page)


@router.post("/service-accounts/{account_id}/disable", response_model=ServiceAccountOut)
def disable_service_account(
    account_id: str,
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
) -> ServiceAccountOut:
    guard(request, ctx, principal, Permission.IDENTITY_WRITE, tenant)
    account = ctx.store.get_record(tenant.value, EntityKind.SERVICE_ACCOUNT, account_id, ServiceAccount)
    updated = ctx.store.put_record(account.with_update(status="disabled"))
    ctx.audit.record(
        tenant_id=tenant.value,
        principal=principal,
        action="service_account.disable",
        category=AuditCategory.ADMIN,
        resource_type="service_account",
        resource_id=account.id,
        request_id=request_id_of(request),
    )
    return ServiceAccountOut(**_sa_out(updated))


@router.post("/role-bindings", response_model=RoleBindingOut)
def create_role_binding(
    payload: RoleBindingCreate,
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
) -> RoleBindingOut:
    guard(request, ctx, principal, Permission.IDENTITY_WRITE, tenant)
    try:
        kind = PrincipalKind(payload.principal_kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="principal_kind must be user|service_account") from exc
    role = _roles([payload.role])[0]
    # The bound principal must exist inside this tenant (no cross-tenant references).
    if kind is PrincipalKind.USER:
        target = ctx.store.get_record(tenant.value, EntityKind.USER, payload.principal_id, User)
        ctx.store.put_record(target.with_update(roles=sorted({*target.roles, role}, key=lambda item: item.value)))
    else:
        target_sa = ctx.store.get_record(tenant.value, EntityKind.SERVICE_ACCOUNT, payload.principal_id, ServiceAccount)
        ctx.store.put_record(
            target_sa.with_update(roles=sorted({*target_sa.roles, role}, key=lambda item: item.value))
        )
    binding = RoleBinding.create(
        tenant_id=tenant.value,
        created_by=principal.subject,
        principal_id=payload.principal_id,
        principal_kind=kind,
        role=role,
        scope_kind=payload.scope_kind,
        scope_id=payload.scope_id,
    )
    ctx.store.put_record(binding)
    ctx.audit.record(
        tenant_id=tenant.value,
        principal=principal,
        action="role_binding.create",
        category=AuditCategory.ADMIN,
        resource_type="role_binding",
        resource_id=binding.id,
        request_id=request_id_of(request),
        details={"principal_id": payload.principal_id, "role": role.value},
    )
    return RoleBindingOut(
        id=binding.id,
        tenant_id=binding.tenant_id,
        principal_id=binding.principal_id,
        principal_kind=binding.principal_kind.value,
        role=binding.role.value,
        scope_kind=binding.scope_kind,
        scope_id=binding.scope_id,
        created_at=binding.created_at,
    )


@router.get("/role-bindings")
def list_role_bindings(
    request: Request,
    ctx: Annotated[EnterpriseContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(current_principal)],
    tenant: Annotated[TenantId, Depends(tenant_scope)],
    page: Annotated[Page, Depends()],
) -> Any:
    guard(request, ctx, principal, Permission.IDENTITY_READ, tenant)
    rows = ctx.store.list_records(tenant.value, EntityKind.ROLE_BINDING, RoleBinding, RecordQuery())
    return paginate(
        [
            RoleBindingOut(
                id=item.id,
                tenant_id=item.tenant_id,
                principal_id=item.principal_id,
                principal_kind=item.principal_kind.value,
                role=item.role.value,
                scope_kind=item.scope_kind,
                scope_id=item.scope_id,
                created_at=item.created_at,
            )
            for item in rows
        ],
        page,
    )


@router.get("/roles")
def list_roles(principal: Annotated[Principal, Depends(current_principal)]) -> list[dict[str, Any]]:
    from matraix.enterprise.auth.roles import ROLE_PERMISSIONS

    return [
        {"name": role.value, "permissions": sorted(item.value for item in perms)}
        for role, perms in ROLE_PERMISSIONS.items()
    ]
