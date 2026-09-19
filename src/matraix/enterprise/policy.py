"""Policy decision vocabulary and the enterprise policy gateway.

Every execution goes through :func:`evaluate_policy`. The secure default is
:attr:`PolicyDecision.SANDBOX_ONLY`. :func:`default_simulation_decision` remains
the unconditional sandbox helper used by existing tests.

This module does not import provider SDKs or LiteLLM.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any

from matraix.enterprise.ids import PersonaId, TenantId

ALLOW_EXTERNAL_ENV = "MATRIX_ENTERPRISE_ALLOW_EXTERNAL"
ALLOW_LIVE_ENV = "MATRIX_ENTERPRISE_ALLOW_LIVE"
MODEL_ALLOWLIST_ENV = "MATRIX_ENTERPRISE_MODEL_ALLOWLIST"
MODEL_DENYLIST_ENV = "MATRIX_ENTERPRISE_MODEL_DENYLIST"

FORBIDDEN_ACTIONS = frozenset(
    {
        "bypass_policy",
        "leak_classified",
        "exfiltrate",
        "disable_audit",
        "send_email_live",
    }
)
CONSEQUENTIAL_ACTIONS = frozenset(
    {
        "write_crm",
        "write_erp",
        "send_email",
        "deploy",
        "production_write",
        "mutate_production",
    }
)
SANDBOX_DESTINATIONS = frozenset({"sandbox", "local", "mock", "simulation"})


class PolicyDecision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    ALLOW_WITH_REDACTION = "ALLOW_WITH_REDACTION"
    ALLOW_WITH_APPROVAL = "ALLOW_WITH_APPROVAL"
    SANDBOX_ONLY = "SANDBOX_ONLY"


class DataClassification(str, Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


@dataclass(frozen=True, slots=True)
class PolicyRequest:
    """Inputs the policy gateway evaluates before an action."""

    tenant_id: TenantId
    action: str
    resource: str
    persona_id: PersonaId | None = None
    environment: str | None = None
    tool: str | None = None
    model_provider: str | None = None
    data_classification: DataClassification = DataClassification.INTERNAL
    destination: str | None = None
    approved: bool = False
    attributes: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": str(self.tenant_id),
            "action": self.action,
            "resource": self.resource,
            "persona_id": str(self.persona_id) if self.persona_id else None,
            "environment": self.environment,
            "tool": self.tool,
            "model_provider": self.model_provider,
            "data_classification": self.data_classification.value,
            "destination": self.destination,
            "approved": self.approved,
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    """Outcome of :func:`evaluate_policy`."""

    decision: PolicyDecision
    request: PolicyRequest
    reasons: tuple[str, ...]
    redaction_required: bool = False
    approval_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "request": self.request.to_dict(),
            "reasons": list(self.reasons),
            "redaction_required": self.redaction_required,
            "approval_required": self.approval_required,
        }


def default_simulation_decision(request: PolicyRequest) -> PolicyDecision:
    """Secure default: keep enterprise tests inside the simulation sandbox."""
    _ = request
    return PolicyDecision.SANDBOX_ONLY


def _csv_env(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def env_allowed_providers() -> tuple[str, ...]:
    return _csv_env(MODEL_ALLOWLIST_ENV)


def env_denied_providers() -> tuple[str, ...]:
    return _csv_env(MODEL_DENYLIST_ENV)


def env_allow_external() -> bool:
    return _truthy_env(ALLOW_EXTERNAL_ENV)


def env_allow_live() -> bool:
    return _truthy_env(ALLOW_LIVE_ENV)


def _normalize_name(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _is_external_destination(destination: str | None, provider: str | None) -> bool:
    dest = _normalize_name(destination)
    if dest in SANDBOX_DESTINATIONS:
        return False
    if dest == "external":
        return True
    provider_name = _normalize_name(provider)
    if provider_name in {None, "sandbox", "local", "ollama", "local-ollama"}:
        return dest not in {None, *SANDBOX_DESTINATIONS} and dest == "external"
    if dest is None:
        return provider_name not in {"sandbox", "local", "ollama", "local-ollama"}
    return dest not in SANDBOX_DESTINATIONS


def evaluate_policy(request: PolicyRequest, policy: Any | None = None) -> PolicyEvaluation:
    """Decide ALLOW / DENY / redaction / approval / SANDBOX_ONLY.

    ``policy`` is optional and duck-typed (typically :class:`ModelPolicy`).
    Missing policy and unset overrides keep the default ``SANDBOX_ONLY``.
    """
    action = (request.action or "").strip().lower()
    provider = _normalize_name(request.model_provider)
    destination = _normalize_name(request.destination)
    classification = request.data_classification
    external = _is_external_destination(destination, provider)

    allowed = tuple(
        str(item).strip().lower()
        for item in (getattr(policy, "allowed_providers", ()) or ())
        if str(item).strip()
    ) + env_allowed_providers()
    denied = tuple(
        str(item).strip().lower()
        for item in (getattr(policy, "denied_providers", ()) or ())
        if str(item).strip()
    ) + env_denied_providers()
    denied_actions = {
        str(item).strip().lower()
        for item in (getattr(policy, "denied_actions", ()) or ())
        if str(item).strip()
    }
    allow_external = bool(getattr(policy, "allow_external", False)) or env_allow_external()
    allow_live = bool(getattr(policy, "allow_live", False)) or env_allow_live()
    default = getattr(policy, "default_decision", None)
    if not isinstance(default, PolicyDecision):
        default = PolicyDecision.SANDBOX_ONLY

    reasons: list[str] = []

    if action in FORBIDDEN_ACTIONS or action in denied_actions:
        reasons.append(f"action {action!r} is forbidden")
        return PolicyEvaluation(
            decision=PolicyDecision.DENY,
            request=request,
            reasons=tuple(reasons),
        )

    if provider and provider in denied:
        reasons.append(f"provider {provider!r} is on the deny-list")
        return PolicyEvaluation(
            decision=PolicyDecision.DENY,
            request=request,
            reasons=tuple(reasons),
        )

    if provider and allowed and provider not in allowed and provider != "sandbox":
        reasons.append(f"provider {provider!r} is not on the allow-list")
        return PolicyEvaluation(
            decision=PolicyDecision.DENY,
            request=request,
            reasons=tuple(reasons),
        )

    if classification is DataClassification.RESTRICTED and external:
        reasons.append("RESTRICTED data cannot leave the sandbox")
        return PolicyEvaluation(
            decision=PolicyDecision.DENY,
            request=request,
            reasons=tuple(reasons),
        )

    if action in CONSEQUENTIAL_ACTIONS and external:
        if allow_live:
            reasons.append("consequential external action requires human approval")
            return PolicyEvaluation(
                decision=PolicyDecision.ALLOW_WITH_APPROVAL,
                request=request,
                reasons=tuple(reasons),
                approval_required=True,
            )
        reasons.append("consequential live action is sandbox-only until allow_live")
        return PolicyEvaluation(
            decision=PolicyDecision.SANDBOX_ONLY,
            request=request,
            reasons=tuple(reasons),
        )

    if classification is DataClassification.CONFIDENTIAL and external:
        if allow_external:
            reasons.append("CONFIDENTIAL data may leave only with redaction")
            return PolicyEvaluation(
                decision=PolicyDecision.ALLOW_WITH_REDACTION,
                request=request,
                reasons=tuple(reasons),
                redaction_required=True,
            )
        reasons.append("CONFIDENTIAL data stays in the sandbox without allow_external")
        return PolicyEvaluation(
            decision=PolicyDecision.SANDBOX_ONLY,
            request=request,
            reasons=tuple(reasons),
            redaction_required=True,
        )

    if (
        classification is DataClassification.PUBLIC
        and allow_external
        and external
        and action not in CONSEQUENTIAL_ACTIONS
    ):
        reasons.append("PUBLIC data with explicit allow_external")
        return PolicyEvaluation(
            decision=PolicyDecision.ALLOW,
            request=request,
            reasons=tuple(reasons),
        )

    if default is not PolicyDecision.SANDBOX_ONLY and allow_external and external:
        reasons.append(f"tenant default_decision={default.value}")
        return PolicyEvaluation(
            decision=default,
            request=request,
            reasons=tuple(reasons),
            redaction_required=default is PolicyDecision.ALLOW_WITH_REDACTION,
            approval_required=default is PolicyDecision.ALLOW_WITH_APPROVAL,
        )

    reasons.append("default enterprise posture is SANDBOX_ONLY")
    return PolicyEvaluation(
        decision=PolicyDecision.SANDBOX_ONLY,
        request=request,
        reasons=tuple(reasons),
    )
