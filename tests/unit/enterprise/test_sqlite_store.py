"""SQLite persistence round-trips and the store factory."""

from __future__ import annotations

from pathlib import Path

import pytest

from matraix.enterprise import (
    CrossTenantAccessError,
    EnterprisePersona,
    EnterpriseSchemaError,
    Experiment,
    InMemoryEnterpriseStore,
    OrganizationId,
    PersonaId,
    Population,
    PopulationId,
    SqliteEnterpriseStore,
    Tenant,
    TenantId,
    create_tenant_with_default_org,
    new_id,
    open_enterprise_store,
)
from matraix.enterprise.ids import EntityKind, ExperimentId
from matraix.enterprise.migrations import apply_migrations
from matraix.enterprise.policy import PolicyDecision


def _persona(tenant: Tenant, org_id: OrganizationId, *, body: str = "per_0042") -> EnterprisePersona:
    return EnterprisePersona.from_legacy_record(
        tenant_id=tenant.id,
        organization_id=org_id,
        persona_id=PersonaId(tenant.id, body),
        record={
            "persona_id": "0042",
            "version": "1.0",
            "source": "synthetic",
            "display_name": "Casey Brooks",
            "dimensions": {"age_bracket": "25-34", "role_function": "Teaching"},
            "enterprise": {
                "employment": {"department": "Engineering", "role": "Analyst"},
            },
        },
    )


def test_sqlite_round_trip_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "enterprise.sqlite"
    store = SqliteEnterpriseStore(path)
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    population = store.put_population(
        Population(
            id=PopulationId(tenant.id, "pop_workforce"),
            tenant_id=tenant.id,
            organization_id=org.id,
            name="Workforce",
            target_size=10,
        )
    )
    persona = EnterprisePersona.from_legacy_record(
        tenant_id=tenant.id,
        organization_id=org.id,
        persona_id=PersonaId(tenant.id, "per_0042"),
        population_id=population.id,
        record={
            "persona_id": "0042",
            "version": "1.0",
            "source": "synthetic",
            "display_name": "Casey Brooks",
            "dimensions": {"age_bracket": "25-34", "role_function": "Teaching"},
            "enterprise": {
                "employment": {"department": "Engineering", "role": "Analyst"},
            },
        },
    )
    store.put_persona(persona)
    store.close()

    reopened = SqliteEnterpriseStore(path)
    loaded_tenant = reopened.get_tenant(tenant.id)
    assert loaded_tenant.slug == "acme"
    assert loaded_tenant.name == "Acme"
    loaded_pop = reopened.get_population(tenant.id, population.id)
    assert loaded_pop.name == "Workforce"
    assert loaded_pop.target_size == 10
    loaded = reopened.get_persona(tenant.id, persona.id)
    assert loaded.legacy_persona_id == "0042"
    assert loaded.display_name == "Casey Brooks"
    assert loaded.dimensions["role_function"] == "Teaching"
    assert loaded.enterprise is not None
    assert loaded.enterprise.employment["department"] == "Engineering"
    assert loaded.population_id == population.id
    assert [item.id for item in reopened.list_personas(tenant.id)] == [persona.id]
    reopened.close()


def test_sqlite_cross_tenant_get_raises(tmp_path: Path) -> None:
    store = SqliteEnterpriseStore(tmp_path / "iso.sqlite")
    alpha, alpha_org = create_tenant_with_default_org(store, name="Alpha", slug="alpha")
    bravo, _bravo_org = create_tenant_with_default_org(store, name="Bravo", slug="bravo")
    persona = _persona(alpha, alpha_org.id)
    store.put_persona(persona)

    with pytest.raises(CrossTenantAccessError):
        store.get_persona(bravo.id, persona.id)
    assert store.list_personas(bravo.id) == []
    store.close()


def test_sqlite_lists_do_not_leak_populations(tmp_path: Path) -> None:
    store = SqliteEnterpriseStore(tmp_path / "pops.sqlite")
    alpha, alpha_org = create_tenant_with_default_org(store, name="Alpha", slug="alpha")
    bravo, bravo_org = create_tenant_with_default_org(store, name="Bravo", slug="bravo")
    store.put_population(
        Population(
            id=PopulationId(alpha.id, "pop_a"),
            tenant_id=alpha.id,
            organization_id=alpha_org.id,
            name="Alpha workforce",
        )
    )
    store.put_population(
        Population(
            id=PopulationId(bravo.id, "pop_b"),
            tenant_id=bravo.id,
            organization_id=bravo_org.id,
            name="Bravo workforce",
        )
    )
    assert [item.name for item in store.list_populations(alpha.id)] == ["Alpha workforce"]
    store.close()


def test_sqlite_experiment_round_trip(tmp_path: Path) -> None:
    store = SqliteEnterpriseStore(tmp_path / "exp.sqlite")
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    population = store.put_population(
        Population(
            id=PopulationId(tenant.id, "pop_a"),
            tenant_id=tenant.id,
            organization_id=org.id,
            name="Workforce",
        )
    )
    experiment = Experiment(
        id=ExperimentId(tenant.id, new_id(EntityKind.EXPERIMENT)),
        tenant_id=tenant.id,
        organization_id=org.id,
        hypothesis="H",
        objective="O",
        population_ids=(population.id,),
    )
    store.put_experiment(experiment)
    loaded = store.get_experiment(tenant.id, experiment.id)
    assert loaded.hypothesis == "H"
    assert loaded.default_policy is PolicyDecision.SANDBOX_ONLY
    assert loaded.population_ids == (population.id,)
    store.close()


def test_duplicate_slug_rejected_on_both_backends(tmp_path: Path) -> None:
    for store in (
        InMemoryEnterpriseStore(),
        SqliteEnterpriseStore(tmp_path / "slug.sqlite"),
    ):
        store.put_tenant(Tenant(id=TenantId("tnt_one"), name="One", slug="shared"))
        with pytest.raises(EnterpriseSchemaError, match="already exists"):
            store.put_tenant(Tenant(id=TenantId("tnt_two"), name="Two", slug="shared"))
        if hasattr(store, "close"):
            store.close()


def test_open_enterprise_store_defaults_to_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MATRIX_ENTERPRISE_STORE", raising=False)
    store = open_enterprise_store()
    assert isinstance(store, InMemoryEnterpriseStore)


def test_open_enterprise_store_sqlite_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "from-env.sqlite"
    monkeypatch.setenv("MATRIX_ENTERPRISE_STORE", "sqlite")
    monkeypatch.setenv("MATRIX_ENTERPRISE_DB", str(path))
    store = open_enterprise_store()
    assert isinstance(store, SqliteEnterpriseStore)
    create_tenant_with_default_org(store, name="Env", slug="env")
    store.close()
    assert path.is_file()


def test_open_enterprise_store_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown enterprise store"):
        open_enterprise_store(backend="postgres")


def test_sqlite_org_edge_and_declaration_survive_reopen(tmp_path: Path) -> None:
    from matraix.enterprise import (
        GenerationBackend,
        OrgEdge,
        OrgEdgeId,
        OrgNodeKind,
        OrgRelation,
        Population,
        PopulationId,
        PopulationSegment,
        build_population_declaration,
    )
    from matraix.enterprise.ids import EntityKind

    path = tmp_path / "graph-decl.sqlite"
    store = SqliteEnterpriseStore(path)
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    persona = _persona(tenant, org.id)
    store.put_persona(persona)
    edge = store.put_org_edge(
        OrgEdge(
            id=OrgEdgeId(tenant.id, new_id(EntityKind.ORG_EDGE)),
            tenant_id=tenant.id,
            organization_id=org.id,
            relation=OrgRelation.SERVES,
            source_kind=OrgNodeKind.PERSONA,
            source_id=persona.id.value,
            target_kind=OrgNodeKind.ORGANIZATION,
            target_id=org.id.value,
        )
    )
    population = store.put_population(
        Population(
            id=PopulationId(tenant.id, "pop_10k"),
            tenant_id=tenant.id,
            organization_id=org.id,
            name="10k",
            target_size=10_000,
        )
    )
    store.put_population_declaration(
        build_population_declaration(
            tenant_id=tenant.id,
            organization_id=org.id,
            population_id=population.id,
            target_size=10_000,
            backend=GenerationBackend.TREIVER,
            segments=(PopulationSegment(name="all", count=10_000),),
        )
    )
    store.close()
    reopened = SqliteEnterpriseStore(path)
    assert reopened.get_org_edge(tenant.id, edge.id).relation is OrgRelation.SERVES
    loaded = reopened.get_population_declaration(tenant.id, population.id)
    assert loaded.backend is GenerationBackend.TREIVER
    assert loaded.resolved_counts == (10_000,)
    reopened.close()


def test_apply_migrations_is_idempotent(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "mig.sqlite"
    conn = sqlite3.connect(path)
    first = apply_migrations(conn)
    second = apply_migrations(conn)
    assert first == [1, 2, 3, 4, 5, 6]
    assert second == []
    conn.close()
