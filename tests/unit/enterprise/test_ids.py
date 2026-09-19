from __future__ import annotations

import pytest

from matraix.enterprise import (
    EnterpriseSchemaError,
    OrganizationId,
    PersonaId,
    TenantId,
    new_id,
)
from matraix.enterprise.ids import EntityKind


def test_tenant_id_normalizes_and_rejects_empty() -> None:
    assert TenantId("TNT_Acme").value == "tnt_acme"
    with pytest.raises(EnterpriseSchemaError):
        TenantId("")
    with pytest.raises(EnterpriseSchemaError):
        TenantId("has spaces")


def test_scoped_ids_embed_tenant_and_are_distinct_types() -> None:
    tenant = TenantId("tnt_one")
    persona = PersonaId(tenant, new_id(EntityKind.PERSONA))
    organization = OrganizationId(tenant, new_id(EntityKind.ORGANIZATION))
    assert persona.belongs_to(tenant)
    assert persona.kind is EntityKind.PERSONA
    assert organization.kind is EntityKind.ORGANIZATION
    assert type(persona) is not type(organization)
    other = TenantId("tnt_two")
    assert not persona.belongs_to(other)


def test_scoped_id_requires_tenant_id() -> None:
    with pytest.raises(EnterpriseSchemaError):
        PersonaId("tnt_one", "per_abc")  # type: ignore[arg-type]
