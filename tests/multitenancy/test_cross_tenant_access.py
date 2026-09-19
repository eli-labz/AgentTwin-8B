"""Cross-tenant access must fail and must be impossible by construction."""

from __future__ import annotations

import pytest

from matraix.enterprise import (
    CrossTenantAccessError,
    EnterprisePersona,
    EnterpriseSchemaError,
    Experiment,
    InMemoryEnterpriseStore,
    Organization,
    OrganizationId,
    PersonaId,
    Population,
    PopulationId,
    Tenant,
    TenantId,
    new_id,
)
from matraix.enterprise.ids import EntityKind, ExperimentId


def _seed_two_tenants() -> tuple[InMemoryEnterpriseStore, Tenant, Tenant]:
    store = InMemoryEnterpriseStore()
    alpha = Tenant(id=TenantId("tnt_alpha"), name="Alpha", slug="alpha")
    bravo = Tenant(id=TenantId("tnt_bravo"), name="Bravo", slug="bravo")
    store.put_tenant(alpha)
    store.put_tenant(bravo)
    store.put_organization(
        Organization(
            id=OrganizationId(alpha.id, "org_alpha"),
            tenant_id=alpha.id,
            name="Alpha Org",
        )
    )
    store.put_organization(
        Organization(
            id=OrganizationId(bravo.id, "org_bravo"),
            tenant_id=bravo.id,
            name="Bravo Org",
        )
    )
    return store, alpha, bravo


def _persona_for(tenant: Tenant, org_id: OrganizationId) -> EnterprisePersona:
    return EnterprisePersona.from_legacy_record(
        tenant_id=tenant.id,
        organization_id=org_id,
        persona_id=PersonaId(tenant.id, "per_shared_looking"),
        record={
            "persona_id": "0042",
            "version": "1.0",
            "source": "synthetic",
            "dimensions": {"age_bracket": "25-34", "role_function": "Teaching"},
        },
    )


def test_get_persona_with_foreign_tenant_id_fails() -> None:
    store, alpha, bravo = _seed_two_tenants()
    persona = _persona_for(alpha, OrganizationId(alpha.id, "org_alpha"))
    store.put_persona(persona)

    with pytest.raises(CrossTenantAccessError):
        store.get_persona(bravo.id, persona.id)


def test_same_local_id_in_other_tenant_is_a_different_record() -> None:
    store, alpha, bravo = _seed_two_tenants()
    alpha_persona = _persona_for(alpha, OrganizationId(alpha.id, "org_alpha"))
    bravo_persona = _persona_for(bravo, OrganizationId(bravo.id, "org_bravo"))
    store.put_persona(alpha_persona)
    store.put_persona(bravo_persona)

    assert store.get_persona(alpha.id, alpha_persona.id).tenant_id == alpha.id
    assert store.get_persona(bravo.id, bravo_persona.id).tenant_id == bravo.id
    assert store.list_personas(alpha.id) == [alpha_persona]
    persona_ids = [item.id for item in store.list_personas(bravo.id)]
    assert bravo_persona.id in persona_ids
    assert alpha_persona.id not in persona_ids


def test_cannot_construct_persona_with_mismatched_ids() -> None:
    alpha = TenantId("tnt_alpha")
    bravo = TenantId("tnt_bravo")
    with pytest.raises(EnterpriseSchemaError, match="not in this tenant"):
        EnterprisePersona.from_legacy_record(
            tenant_id=alpha,
            organization_id=OrganizationId(bravo, "org_bravo"),
            record={
                "persona_id": "1",
                "dimensions": {"age_bracket": "25-34"},
            },
        )


def test_experiment_cannot_attach_foreign_population() -> None:
    alpha = TenantId("tnt_alpha")
    bravo = TenantId("tnt_bravo")
    with pytest.raises(EnterpriseSchemaError, match="not in this tenant"):
        Experiment(
            id=ExperimentId(alpha, new_id(EntityKind.EXPERIMENT)),
            tenant_id=alpha,
            organization_id=OrganizationId(alpha, "org_alpha"),
            hypothesis="H",
            objective="O",
            population_ids=(PopulationId(bravo, "pop_foreign"),),
        )


def test_store_does_not_return_foreign_population_on_list() -> None:
    store, alpha, bravo = _seed_two_tenants()
    store.put_population(
        Population(
            id=PopulationId(alpha.id, "pop_a"),
            tenant_id=alpha.id,
            organization_id=OrganizationId(alpha.id, "org_alpha"),
            name="Alpha workforce",
        )
    )
    store.put_population(
        Population(
            id=PopulationId(bravo.id, "pop_b"),
            tenant_id=bravo.id,
            organization_id=OrganizationId(bravo.id, "org_bravo"),
            name="Bravo workforce",
        )
    )
    names = [item.name for item in store.list_populations(alpha.id)]
    assert names == ["Alpha workforce"]
