"""Policy decision vocabulary for enterprise executions.

This slice defines the decision space and a request record. Enforcement inside
Harbor trials is a later phase. Default enterprise posture is simulation-only
(:attr:`PolicyDecision.SANDBOX_ONLY`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from matraix.enterprise.ids import PersonaId, TenantId


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
    """Inputs a future policy engine will evaluate before an action."""

    tenant_id: TenantId
    action: str
    resource: str
    persona_id: PersonaId | None = None
    environment: str | None = None
    tool: str | None = None
    model_provider: str | None = None
    data_classification: DataClassification = DataClassification.INTERNAL
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
            "attributes": dict(self.attributes),
        }


def default_simulation_decision(request: PolicyRequest) -> PolicyDecision:
    """Secure default: keep enterprise tests inside the simulation sandbox."""
    _ = request
    return PolicyDecision.SANDBOX_ONLY
