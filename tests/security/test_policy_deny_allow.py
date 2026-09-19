"""Deny and allow paths through the enterprise policy gateway."""

from __future__ import annotations

from matraix.enterprise.ids import TenantId
from matraix.enterprise.model_gateway import ModelPolicy
from matraix.enterprise.policy import (
    DataClassification,
    PolicyDecision,
    PolicyRequest,
    default_simulation_decision,
    evaluate_policy,
)


def _request(**kwargs) -> PolicyRequest:
    defaults = dict(
        tenant_id=TenantId("tnt_acme"),
        action="complete",
        resource="model.complete",
        data_classification=DataClassification.INTERNAL,
    )
    defaults.update(kwargs)
    return PolicyRequest(**defaults)


def test_evaluate_defaults_to_sandbox_only() -> None:
    evaluation = evaluate_policy(_request(action="call_external_api", resource="crm.contacts"))
    assert evaluation.decision is PolicyDecision.SANDBOX_ONLY
    assert default_simulation_decision(_request(action="call_external_api", resource="crm")) is (
        PolicyDecision.SANDBOX_ONLY
    )


def test_forbidden_action_is_deny() -> None:
    evaluation = evaluate_policy(_request(action="bypass_policy", resource="policy"))
    assert evaluation.decision is PolicyDecision.DENY
    assert "forbidden" in evaluation.reasons[0]


def test_restricted_plus_external_provider_is_deny() -> None:
    evaluation = evaluate_policy(
        _request(
            model_provider="anthropic",
            destination="external",
            data_classification=DataClassification.RESTRICTED,
        )
    )
    assert evaluation.decision is PolicyDecision.DENY
    assert any("RESTRICTED" in reason for reason in evaluation.reasons)


def test_denied_provider_is_deny() -> None:
    policy = ModelPolicy(tenant_id=TenantId("tnt_acme"), denied_providers=("openai",))
    evaluation = evaluate_policy(_request(model_provider="openai"), policy)
    assert evaluation.decision is PolicyDecision.DENY


def test_public_allow_external_is_allow() -> None:
    policy = ModelPolicy(
        tenant_id=TenantId("tnt_acme"),
        allow_external=True,
        allowed_providers=("anthropic",),
    )
    evaluation = evaluate_policy(
        _request(
            model_provider="anthropic",
            destination="external",
            data_classification=DataClassification.PUBLIC,
        ),
        policy,
    )
    assert evaluation.decision is PolicyDecision.ALLOW


def test_confidential_allow_external_is_redaction() -> None:
    policy = ModelPolicy(tenant_id=TenantId("tnt_acme"), allow_external=True)
    evaluation = evaluate_policy(
        _request(
            model_provider="anthropic",
            destination="external",
            data_classification=DataClassification.CONFIDENTIAL,
        ),
        policy,
    )
    assert evaluation.decision is PolicyDecision.ALLOW_WITH_REDACTION
    assert evaluation.redaction_required is True


def test_live_write_without_allow_live_stays_sandbox() -> None:
    evaluation = evaluate_policy(
        _request(action="write_crm", resource="crm.contacts", model_provider="openai")
    )
    assert evaluation.decision is PolicyDecision.SANDBOX_ONLY


def test_env_denylist_denies(monkeypatch) -> None:
    monkeypatch.setenv("MATRIX_ENTERPRISE_MODEL_DENYLIST", "gemini,openai")
    evaluation = evaluate_policy(_request(model_provider="gemini"))
    assert evaluation.decision is PolicyDecision.DENY
