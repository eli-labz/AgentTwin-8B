from __future__ import annotations

from matraix.enterprise.ids import TenantId
from matraix.enterprise.policy import (
    DataClassification,
    PolicyDecision,
    PolicyRequest,
    default_simulation_decision,
)


def test_default_enterprise_action_is_sandbox_only() -> None:
    request = PolicyRequest(
        tenant_id=TenantId("tnt_acme"),
        action="call_external_api",
        resource="crm.contacts",
        data_classification=DataClassification.CONFIDENTIAL,
    )
    assert default_simulation_decision(request) is PolicyDecision.SANDBOX_ONLY
    assert set(PolicyDecision) == {
        PolicyDecision.ALLOW,
        PolicyDecision.DENY,
        PolicyDecision.ALLOW_WITH_REDACTION,
        PolicyDecision.ALLOW_WITH_APPROVAL,
        PolicyDecision.SANDBOX_ONLY,
    }
