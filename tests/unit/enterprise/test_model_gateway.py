"""Model gateway routing, completion, and catalog — no provider SDKs."""

from __future__ import annotations

from pathlib import Path

import pytest

from matraix.enterprise import (
    ApprovalRequiredError,
    DataClassification,
    EntityNotFoundError,
    InMemoryEnterpriseStore,
    ModelGateway,
    ModelPolicy,
    ModelRequest,
    PolicyDecision,
    PolicyDeniedError,
    SqliteEnterpriseStore,
    TenantId,
    complete_model,
    create_tenant_with_default_org,
    default_model_policy,
    evaluate_policy,
    route_model,
)
from matraix.enterprise.model_gateway import DEFAULT_MODEL_CATALOG


def test_enterprise_package_does_not_import_litellm() -> None:
    import matraix.enterprise as pkg
    import matraix.enterprise.model_gateway as gateway
    import matraix.enterprise.policy as policy

    assert "litellm" not in pkg.__dict__
    assert "litellm" not in gateway.__dict__
    assert "litellm" not in policy.__dict__


def test_default_route_is_sandbox_only() -> None:
    request = ModelRequest(
        tenant_id=TenantId("tnt_acme"),
        messages=({"role": "user", "content": "hello"},),
        model_provider="anthropic",
    )
    routed = route_model(request)
    assert routed.decision is PolicyDecision.SANDBOX_ONLY
    assert routed.provider is not None
    assert routed.provider.name == "sandbox"


def test_complete_sandbox_does_not_call_provider() -> None:
    response = complete_model(
        ModelRequest(
            tenant_id=TenantId("tnt_acme"),
            messages=({"role": "user", "content": "hello workforce"},),
        )
    )
    assert response.decision is PolicyDecision.SANDBOX_ONLY
    assert response.provider == "sandbox"
    assert "hello workforce" in response.content
    assert response.usage.estimated_cost_usd == 0.0
    assert response.dry_run is False


def test_deny_listed_provider_is_denied() -> None:
    policy = ModelPolicy(
        tenant_id=TenantId("tnt_acme"),
        denied_providers=("openai",),
    )
    request = ModelRequest(
        tenant_id=TenantId("tnt_acme"),
        model_provider="openai",
        data_classification=DataClassification.PUBLIC,
    )
    routed = route_model(request, policy)
    assert routed.decision is PolicyDecision.DENY
    assert routed.provider is None
    with pytest.raises(PolicyDeniedError, match="deny-list"):
        complete_model(request, policy)


def test_allow_list_rejects_other_providers() -> None:
    policy = ModelPolicy(
        tenant_id=TenantId("tnt_acme"),
        allowed_providers=("anthropic",),
        allow_external=True,
    )
    denied = route_model(
        ModelRequest(
            tenant_id=TenantId("tnt_acme"),
            model_provider="openai",
            data_classification=DataClassification.PUBLIC,
        ),
        policy,
    )
    assert denied.decision is PolicyDecision.DENY


def test_allow_path_selects_catalog_provider() -> None:
    policy = ModelPolicy(
        tenant_id=TenantId("tnt_acme"),
        allowed_providers=("anthropic", "openai"),
        allow_external=True,
    )
    request = ModelRequest(
        tenant_id=TenantId("tnt_acme"),
        model_provider="anthropic",
        data_classification=DataClassification.PUBLIC,
        action="complete",
        task_complexity="complex",
    )
    routed = route_model(request, policy)
    assert routed.decision is PolicyDecision.ALLOW
    assert routed.provider is not None
    assert routed.provider.name == "anthropic"
    response = complete_model(request, policy)
    assert response.decision is PolicyDecision.ALLOW
    assert response.provider == "anthropic"
    assert response.dry_run is True
    assert response.content == ""


def test_residency_and_cost_filters_prefer_matching_provider() -> None:
    policy = ModelPolicy(
        tenant_id=TenantId("tnt_acme"),
        allow_external=True,
        required_residency="eu",
        max_cost_score=0.6,
        max_latency_ms=2000,
    )
    request = ModelRequest(
        tenant_id=TenantId("tnt_acme"),
        data_classification=DataClassification.PUBLIC,
        required_capability="chat",
        task_complexity="simple",
        destination="external",
    )
    routed = route_model(request, policy)
    assert routed.decision is PolicyDecision.ALLOW
    assert routed.provider is not None
    assert routed.provider.residency == "eu"
    assert routed.provider.name == "eu-anthropic"


def test_restricted_external_is_denied_before_routing() -> None:
    request = ModelRequest(
        tenant_id=TenantId("tnt_acme"),
        model_provider="anthropic",
        data_classification=DataClassification.RESTRICTED,
    )
    routed = route_model(request)
    assert routed.decision is PolicyDecision.DENY
    with pytest.raises(PolicyDeniedError, match="RESTRICTED"):
        complete_model(request)


def test_confidential_external_redacts_in_sandbox() -> None:
    policy = ModelPolicy(tenant_id=TenantId("tnt_acme"), allow_external=True)
    request = ModelRequest(
        tenant_id=TenantId("tnt_acme"),
        model_provider="anthropic",
        data_classification=DataClassification.CONFIDENTIAL,
        messages=({"role": "user", "content": "use sk-secretvalue99 token=abc"},),
    )
    evaluation = evaluate_policy(request.to_policy_request(destination="external"), policy)
    assert evaluation.decision is PolicyDecision.ALLOW_WITH_REDACTION
    response = complete_model(request, policy)
    assert response.decision is PolicyDecision.ALLOW_WITH_REDACTION
    assert response.provider == "sandbox"
    assert "sk-secretvalue99" not in response.content
    assert "[REDACTED]" in response.content
    assert response.redacted is True


def test_consequential_action_requires_approval() -> None:
    policy = ModelPolicy(
        tenant_id=TenantId("tnt_acme"),
        allow_external=True,
        allow_live=True,
    )
    request = ModelRequest(
        tenant_id=TenantId("tnt_acme"),
        action="write_crm",
        resource="crm.contacts",
        model_provider="openai",
        data_classification=DataClassification.PUBLIC,
    )
    routed = route_model(request, policy)
    assert routed.decision is PolicyDecision.ALLOW_WITH_APPROVAL
    with pytest.raises(ApprovalRequiredError, match="approval"):
        complete_model(request, policy)
    approved = complete_model(
        ModelRequest(
            tenant_id=TenantId("tnt_acme"),
            action="write_crm",
            resource="crm.contacts",
            model_provider="openai",
            data_classification=DataClassification.PUBLIC,
            approved=True,
            messages=({"role": "user", "content": "update contact"},),
        ),
        policy,
    )
    assert approved.decision is PolicyDecision.ALLOW_WITH_APPROVAL
    assert approved.provider == "sandbox"
    assert approved.held_for_approval is True


def test_injected_completer_used_only_on_allow() -> None:
    class _Stub:
        def complete(self, request, provider):
            from matraix.enterprise.model_gateway import ModelResponse, ModelUsage

            return ModelResponse(
                content=f"from-{provider.name}",
                provider=provider.name,
                decision=PolicyDecision.ALLOW,
                usage=ModelUsage(total_tokens=3),
            )

    policy = ModelPolicy(
        tenant_id=TenantId("tnt_acme"),
        allow_external=True,
        allowed_providers=("gemini",),
    )
    gateway = ModelGateway(completer=_Stub())
    response = gateway.complete(
        ModelRequest(
            tenant_id=TenantId("tnt_acme"),
            model_provider="gemini",
            data_classification=DataClassification.PUBLIC,
        ),
        policy,
    )
    assert response.content == "from-gemini"
    assert response.decision is PolicyDecision.ALLOW
    assert response.dry_run is False


def test_model_policy_round_trip_and_isolation(tmp_path: Path) -> None:
    store = SqliteEnterpriseStore(tmp_path / "policy.sqlite")
    alpha, _ = create_tenant_with_default_org(store, name="Alpha", slug="alpha")
    bravo, _ = create_tenant_with_default_org(store, name="Bravo", slug="bravo")
    stored = store.put_model_policy(
        ModelPolicy(
            tenant_id=alpha.id,
            allowed_providers=("anthropic",),
            denied_providers=("openai",),
            required_residency="us",
            allow_external=True,
            max_cost_score=0.5,
            max_latency_ms=1000,
        )
    )
    assert stored.allow_external is True
    store.close()

    reopened = SqliteEnterpriseStore(tmp_path / "policy.sqlite")
    loaded = reopened.get_model_policy(alpha.id)
    assert loaded.allowed_providers == ("anthropic",)
    assert loaded.denied_providers == ("openai",)
    assert loaded.allow_external is True
    assert loaded.required_residency == "us"
    other = reopened.get_model_policy(bravo.id)
    assert other.allow_external is False
    assert other.default_decision is PolicyDecision.SANDBOX_ONLY
    assert other.allowed_providers == ()
    reopened.close()


def test_memory_store_policy_defaults_sandbox() -> None:
    store = InMemoryEnterpriseStore()
    tenant, _ = create_tenant_with_default_org(store, name="Acme", slug="acme")
    policy = store.get_model_policy(tenant.id)
    assert policy == default_model_policy(tenant.id)
    assert policy.default_decision is PolicyDecision.SANDBOX_ONLY


def test_unknown_tenant_policy_raises() -> None:
    store = InMemoryEnterpriseStore()
    with pytest.raises(EntityNotFoundError, match="unknown tenant"):
        store.get_model_policy(TenantId("tnt_missing"))


def test_catalog_names_are_stable() -> None:
    names = {item.name for item in DEFAULT_MODEL_CATALOG}
    assert {"sandbox", "anthropic", "openai", "gemini", "eu-anthropic", "local-ollama"} <= names
    sandbox = next(item for item in DEFAULT_MODEL_CATALOG if item.name == "sandbox")
    assert sandbox.credential_env is None
    assert sandbox.destination == "sandbox"
