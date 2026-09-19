"""Population declaration validation (10k-shaped, no synthesis rewrite)."""

from __future__ import annotations

from pathlib import Path

import pytest

from matraix.enterprise import (
    CrossTenantAccessError,
    EnterpriseSchemaError,
    GenerationBackend,
    InMemoryEnterpriseStore,
    Population,
    PopulationId,
    PopulationSegment,
    SqliteEnterpriseStore,
    build_population_declaration,
    create_tenant_with_default_org,
)


def _attach_population(store, tenant, org, body: str = "pop_workforce"):
    return store.put_population(
        Population(
            id=PopulationId(tenant.id, body),
            tenant_id=tenant.id,
            organization_id=org.id,
            name="Workforce",
            target_size=10_000,
        )
    )


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path: Path):
    if request.param == "memory":
        backend = InMemoryEnterpriseStore()
    else:
        backend = SqliteEnterpriseStore(tmp_path / "pop.sqlite")
    yield backend
    backend.close()


def test_declare_10k_shaped_population(store) -> None:
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    population = _attach_population(store, tenant, org)
    declaration = build_population_declaration(
        tenant_id=tenant.id,
        organization_id=org.id,
        population_id=population.id,
        target_size=10_000,
        backend=GenerationBackend.CORESET_1M,
        include_org_structure=True,
        segments=(
            PopulationSegment(
                name="engineering",
                share=0.4,
                filters={"department": ["Engineering"]},
            ),
            PopulationSegment(name="support", share=0.3),
            PopulationSegment(name="other", share=0.3),
        ),
    )
    stored = store.put_population_declaration(declaration)
    assert stored.target_size == 10_000
    assert stored.backend is GenerationBackend.CORESET_1M
    assert stored.resolved_counts == (4000, 3000, 3000)
    assert sum(stored.resolved_counts) == 10_000
    assert stored.privacy_mode == "aggregate_stats_then_synthetic"

    loaded = store.get_population_declaration(tenant.id, population.id)
    assert loaded.resolved_counts == (4000, 3000, 3000)
    assert loaded.segments[0].filters["department"] == ("Engineering",)
    assert loaded.include_org_structure is True


def test_count_segments_must_sum_to_target() -> None:
    tenant, org = create_tenant_with_default_org(
        InMemoryEnterpriseStore(), name="Acme", slug="acme"
    )
    population = Population(
        id=PopulationId(tenant.id, "pop_a"),
        tenant_id=tenant.id,
        organization_id=org.id,
        name="A",
        target_size=100,
    )
    with pytest.raises(EnterpriseSchemaError, match="sum to"):
        build_population_declaration(
            tenant_id=tenant.id,
            organization_id=org.id,
            population_id=population.id,
            target_size=100,
            backend="full_dag",
            segments=(
                PopulationSegment(name="a", count=60),
                PopulationSegment(name="b", count=30),
            ),
        )


def test_forbids_identifiable_employee_cloning() -> None:
    tenant, org = create_tenant_with_default_org(
        InMemoryEnterpriseStore(), name="Acme", slug="acme"
    )
    population = Population(
        id=PopulationId(tenant.id, "pop_a"),
        tenant_id=tenant.id,
        organization_id=org.id,
        name="A",
    )
    with pytest.raises(EnterpriseSchemaError, match="identifiable employees"):
        build_population_declaration(
            tenant_id=tenant.id,
            organization_id=org.id,
            population_id=population.id,
            target_size=10,
            backend="treiver",
            segments=(PopulationSegment(name="all", count=10),),
            privacy_mode="clone_identifiable_employees",
        )


def test_unknown_backend_and_catalog_filter_fail() -> None:
    tenant, org = create_tenant_with_default_org(
        InMemoryEnterpriseStore(), name="Acme", slug="acme"
    )
    population = Population(
        id=PopulationId(tenant.id, "pop_a"),
        tenant_id=tenant.id,
        organization_id=org.id,
        name="A",
    )
    with pytest.raises(EnterpriseSchemaError, match="backend"):
        build_population_declaration(
            tenant_id=tenant.id,
            organization_id=org.id,
            population_id=population.id,
            target_size=1,
            backend="rewrite-synthesis",
            segments=(PopulationSegment(name="all", count=1),),
        )
    with pytest.raises(EnterpriseSchemaError, match="not in catalog"):
        PopulationSegment(
            name="bad",
            share=1.0,
            filters={"age_bracket": ["not-a-bracket"]},
        )


def test_declaration_isolation(store) -> None:
    alpha, alpha_org = create_tenant_with_default_org(store, name="Alpha", slug="alpha")
    bravo, _bravo_org = create_tenant_with_default_org(store, name="Bravo", slug="bravo")
    population = _attach_population(store, alpha, alpha_org, "pop_alpha")
    store.put_population_declaration(
        build_population_declaration(
            tenant_id=alpha.id,
            organization_id=alpha_org.id,
            population_id=population.id,
            target_size=10_000,
            backend="coreset_1m",
            segments=(
                PopulationSegment(name="a", count=6000),
                PopulationSegment(name="b", count=4000),
            ),
        )
    )
    with pytest.raises(CrossTenantAccessError):
        store.get_population_declaration(bravo.id, population.id)
