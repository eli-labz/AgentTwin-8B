from __future__ import annotations

import pytest

from matraix.enterprise import (
    EnterprisePersona,
    EnterpriseSchemaError,
    Experiment,
    ExperimentId,
    Organization,
    OrganizationId,
    TenantId,
    new_id,
)
from matraix.enterprise.ids import EntityKind
from matraix.enterprise.policy import PolicyDecision


def test_organization_rejects_cross_tenant_parent() -> None:
    tenant = TenantId("tnt_a")
    other = TenantId("tnt_b")
    with pytest.raises(EnterpriseSchemaError, match="not in this tenant"):
        Organization(
            id=OrganizationId(tenant, new_id(EntityKind.ORGANIZATION)),
            tenant_id=tenant,
            name="Acme",
            parent_id=OrganizationId(other, new_id(EntityKind.ORGANIZATION)),
        )


def test_experiment_defaults_to_sandbox_only() -> None:
    tenant = TenantId("tnt_a")
    org = OrganizationId(tenant, new_id(EntityKind.ORGANIZATION))
    experiment = Experiment(
        id=ExperimentId(tenant, new_id(EntityKind.EXPERIMENT)),
        tenant_id=tenant,
        organization_id=org,
        hypothesis="Novice users retry more often",
        objective="Measure retry rate by AI literacy",
    )
    assert experiment.default_policy is PolicyDecision.SANDBOX_ONLY


def test_persona_from_legacy_record_preserves_existing_fields() -> None:
    tenant = TenantId("tnt_a")
    org = OrganizationId(tenant, new_id(EntityKind.ORGANIZATION))
    persona = EnterprisePersona.from_legacy_record(
        tenant_id=tenant,
        organization_id=org,
        record={
            "persona_id": "0042",
            "version": "1.0",
            "source": "amazon",
            "display_name": "Casey Brooks",
            "dimensions": {
                "age_bracket": "25-34",
                "life_stage": "Early career",
                "seniority": "Entry",
                "years_experience": "0-2",
                "highest_education": "Bachelor's",
                "role_function": "Teaching",
            },
            "enterprise": {
                "employment": {"department": "Engineering", "role": "Analyst"},
            },
        },
    )
    assert persona.legacy_persona_id == "0042"
    assert persona.display_name == "Casey Brooks"
    assert persona.dimensions["role_function"] == "Teaching"
    assert persona.enterprise is not None
    assert persona.enterprise.employment["department"] == "Engineering"
    assert persona.id.belongs_to(tenant)
