"""Authentication providers and least-privilege RBAC."""

from __future__ import annotations

import json
import time

import pytest

from matraix.enterprise.auth import (
    AuthenticationError,
    AuthorizationError,
    ChainAuthProvider,
    LocalBearerAuthProvider,
    OidcConfig,
    OidcJwtAuthProvider,
    Permission,
    Principal,
    ServiceAccountAuthProvider,
    authorize,
    build_auth_provider_from_env,
    generate_token,
    hash_token,
    permissions_for,
    token_prefix,
)
from matraix.enterprise.domain import PrincipalKind, RoleName, ServiceAccount
from matraix.enterprise.repositories import InMemoryEnterpriseStore
from matraix.enterprise.secrets import StaticSecretProvider
from matraix.enterprise.store import create_tenant_with_default_org

jwt = pytest.importorskip("jwt")


def test_role_matrix_is_least_privilege() -> None:
    viewer = permissions_for([RoleName.VIEWER])
    analyst = permissions_for([RoleName.ANALYST])
    researcher = permissions_for([RoleName.RESEARCHER])
    operator = permissions_for([RoleName.OPERATOR])
    admin = permissions_for([RoleName.ADMIN])
    assert viewer < analyst < researcher < operator < admin
    assert Permission.EXPERIMENT_LAUNCH not in viewer
    assert Permission.EXPERIMENT_LAUNCH in researcher
    assert Permission.EXPERIMENT_APPROVE not in researcher
    assert Permission.EXPERIMENT_APPROVE in operator
    assert Permission.IDENTITY_WRITE not in operator
    assert Permission.IDENTITY_WRITE in admin
    assert Permission.AUDIT_READ not in researcher and Permission.AUDIT_READ in operator


def test_authorize_enforces_permission_and_tenant() -> None:
    principal = Principal(subject="u", kind=PrincipalKind.USER, tenant_id="tnt_a", roles=frozenset({RoleName.RESEARCHER}))
    authorize(principal, Permission.EXPERIMENT_LAUNCH, "tnt_a")
    with pytest.raises(AuthorizationError):
        authorize(principal, Permission.EXPERIMENT_APPROVE, "tnt_a")
    with pytest.raises(AuthorizationError):
        authorize(principal, Permission.EXPERIMENT_READ, "tnt_b")
    root = Principal(subject="root", kind=PrincipalKind.SERVICE_ACCOUNT, tenant_id=None, platform_admin=True)
    authorize(root, Permission.TENANT_MANAGE, "tnt_b")


def test_local_bearer_provider_from_secrets() -> None:
    secrets = StaticSecretProvider(
        {
            "MATRIX_ENTERPRISE_API_TOKEN": "shared-platform-token",
            "MATRIX_ENTERPRISE_AUTH_TOKENS": json.dumps(
                {"alice-token": {"subject": "alice", "tenant_id": "tnt_a", "roles": ["researcher"]}}
            ),
        }
    )
    provider = LocalBearerAuthProvider.from_secret_provider(secrets)
    root = provider.authenticate({"authorization": "Bearer shared-platform-token"})
    assert root is not None and root.platform_admin
    alice = provider.authenticate({"Authorization": "Bearer alice-token"})
    assert alice is not None and alice.roles == frozenset({RoleName.RESEARCHER}) and alice.tenant_id == "tnt_a"
    assert provider.authenticate({"authorization": "Bearer nope"}) is None
    assert provider.authenticate({}) is None


def test_oidc_hs256_and_rs256_verification() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    secret = "0123456789abcdef0123456789abcdef"
    config = OidcConfig(issuer="https://idp.example", audience="agenttwin", hs256_secret=secret, algorithms=("HS256",))
    token = jwt.encode(
        {"iss": "https://idp.example", "aud": "agenttwin", "sub": "bob", "tenant_id": "tnt_a", "roles": ["operator"], "exp": int(time.time()) + 60},
        secret,
        algorithm="HS256",
    )
    principal = OidcJwtAuthProvider(config).authenticate({"authorization": f"Bearer {token}"})
    assert principal is not None and principal.subject == "bob" and RoleName.OPERATOR in principal.roles

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = "k1"
    rs_config = OidcConfig(issuer="https://idp.example", audience="agenttwin", jwks={"keys": [jwk]})
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    good = jwt.encode(
        {"iss": "https://idp.example", "aud": "agenttwin", "sub": "carol", "tenant_id": "tnt_a", "roles": "viewer analyst", "exp": int(time.time()) + 60},
        pem,
        algorithm="RS256",
        headers={"kid": "k1"},
    )
    carol = OidcJwtAuthProvider(rs_config).authenticate({"authorization": f"Bearer {good}"})
    assert carol is not None and carol.roles == frozenset({RoleName.VIEWER, RoleName.ANALYST})

    for claims in (
        {"iss": "https://evil.example", "aud": "agenttwin", "sub": "x", "exp": int(time.time()) + 60},
        {"iss": "https://idp.example", "aud": "other", "sub": "x", "exp": int(time.time()) + 60},
        {"iss": "https://idp.example", "aud": "agenttwin", "sub": "x", "exp": int(time.time()) - 600},
    ):
        bad = jwt.encode(claims, pem, algorithm="RS256", headers={"kid": "k1"})
        with pytest.raises(AuthenticationError):
            OidcJwtAuthProvider(rs_config).authenticate({"authorization": f"Bearer {bad}"})
    # Wrong key
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    forged = jwt.encode({"iss": "https://idp.example", "aud": "agenttwin", "sub": "x", "exp": int(time.time()) + 60}, other_pem, algorithm="RS256", headers={"kid": "k1"})
    with pytest.raises(AuthenticationError):
        OidcJwtAuthProvider(rs_config).authenticate({"authorization": f"Bearer {forged}"})


def test_service_account_tokens_are_hashed_and_revocable() -> None:
    store = InMemoryEnterpriseStore()
    tenant, _ = create_tenant_with_default_org(store, name="A", slug="a")
    raw = generate_token()
    account = ServiceAccount.create(
        tenant_id=tenant.id.value,
        name="ci",
        roles=[RoleName.OPERATOR],
        token_hash=hash_token(raw),
        token_prefix=token_prefix(raw),
    )
    store.put_record(account)
    assert raw not in json.dumps(account.to_document())
    provider = ServiceAccountAuthProvider(store=store, tenant_ids=lambda: [t.id.value for t in store.list_tenants()])
    principal = provider.authenticate({"authorization": f"Bearer {raw}"})
    assert principal is not None and principal.kind is PrincipalKind.SERVICE_ACCOUNT and principal.tenant_id == tenant.id.value
    assert provider.authenticate({"authorization": "Bearer atsk_not-a-real-token-value"}) is None
    store.put_record(account.with_update(status="disabled"))
    with pytest.raises(AuthenticationError):
        provider.authenticate({"authorization": f"Bearer {raw}"})


def test_env_builder_dev_open_and_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MATRIX_ENTERPRISE_AUTH_MODE", raising=False)
    chain = build_auth_provider_from_env(secrets=StaticSecretProvider({}))
    assert isinstance(chain, ChainAuthProvider) and chain.open
    monkeypatch.setenv("MATRIX_ENTERPRISE_AUTH_MODE", "strict")
    with pytest.raises(AuthenticationError):
        build_auth_provider_from_env(secrets=StaticSecretProvider({}))
    configured = build_auth_provider_from_env(secrets=StaticSecretProvider({"MATRIX_ENTERPRISE_API_TOKEN": "t"}))
    assert not configured.open
