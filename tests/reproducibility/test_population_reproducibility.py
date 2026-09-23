"""Reproducibility invariants for the population engine.

* deterministic materialization for identical seed + configuration
* immutable population snapshot once materialized
* reproducible cohort for the same population version + seed
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from matraix.enterprise.domain import PopulationVersion, PopulationVersionStatus
from matraix.enterprise.entities import Population
from matraix.enterprise.errors import EnterpriseSchemaError
from matraix.enterprise.ids import EntityKind, PopulationId, TenantId, new_id
from matraix.enterprise.population import CohortSelection, build_cohort, materialize_population
from matraix.enterprise.population_builder import PopulationSegment, build_population_declaration
from matraix.enterprise.records import RecordQuery
from matraix.enterprise.repositories import InMemoryEnterpriseStore
from matraix.enterprise.store import create_tenant_with_default_org

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_POOL = "persona/datasets/matraix-persona-dev-sample"


def _seed_store(slug: str = "acme"):
    store = InMemoryEnterpriseStore()
    tenant, organization = create_tenant_with_default_org(
        store, name="Acme", slug=slug, tenant_id=TenantId(f"tnt_{slug}")
    )
    population = Population(
        id=PopulationId(tenant.id, new_id(EntityKind.POPULATION)),
        tenant_id=tenant.id,
        organization_id=organization.id,
        name="Workforce",
        target_size=10,
    )
    store.put_population(population)
    return store, tenant, organization, population


def _declare(store, tenant, organization, population, segments, backend="treiver"):
    declaration = build_population_declaration(
        tenant_id=tenant.id,
        organization_id=organization.id,
        population_id=population.id,
        target_size=sum(int(item.count or 0) for item in segments),
        backend=backend,
        segments=segments,
    )
    return store.put_population_declaration(declaration)


def _segments():
    return [
        PopulationSegment(name="support", count=6),
        PopulationSegment(name="engineering", count=4),
    ]


def _materialize(store, declaration, *, seed: int, artifact_root: Path, **kwargs):
    return materialize_population(
        store,
        declaration=declaration,
        seed=seed,
        artifact_root=artifact_root,
        repo_root=REPO_ROOT,
        evidence_pool=EVIDENCE_POOL,
        **kwargs,
    )


def test_same_seed_reproduces_identical_population(tmp_path: Path) -> None:
    first_store, tenant, organization, population = _seed_store("alpha")
    first = _materialize(
        first_store,
        _declare(first_store, tenant, organization, population, _segments()),
        seed=42,
        artifact_root=tmp_path / "first",
    )
    second_store, tenant2, organization2, population2 = _seed_store("bravo")
    second = _materialize(
        second_store,
        _declare(second_store, tenant2, organization2, population2, _segments()),
        seed=42,
        artifact_root=tmp_path / "second",
    )

    assert first.version.manifest_hash == second.version.manifest_hash
    assert first.version.realized_size == second.version.realized_size == 10
    first_bytes = [Path(path).read_bytes() for path in first.persona_paths]
    second_bytes = [Path(path).read_bytes() for path in second.persona_paths]
    assert first_bytes == second_bytes
    # Quality is a pure function of the records, so it matches too.
    assert first.quality.digest() == second.quality.digest()


def test_different_seed_produces_a_different_population(tmp_path: Path) -> None:
    store, tenant, organization, population = _seed_store("alpha")
    declaration = _declare(store, tenant, organization, population, _segments())
    first = _materialize(store, declaration, seed=42, artifact_root=tmp_path / "a")
    second = _materialize(store, declaration, seed=43, artifact_root=tmp_path / "b")
    assert first.version.manifest_hash != second.version.manifest_hash
    assert second.version.version_number == first.version.version_number + 1


def test_materialized_records_carry_full_provenance(tmp_path: Path) -> None:
    store, tenant, organization, population = _seed_store("alpha")
    result = _materialize(
        store,
        _declare(store, tenant, organization, population, _segments()),
        seed=7,
        artifact_root=tmp_path,
        segment_weights={"support": 1.0, "engineering": 2.5},
    )
    version = result.version
    assert version.status == PopulationVersionStatus.READY.value
    assert version.seed == 7
    assert version.sampling_method == "treiver"
    assert version.generator_version.startswith("agenttwin-population-materializer/")
    assert version.model_version is None  # no LLM on this path
    assert version.provenance["llm_involved"] is False
    assert version.privacy_mode == "aggregate_stats_then_synthetic"
    assert version.schema_version == "1.0"
    assert version.source_datasets and version.source_datasets[0]["path"] == EVIDENCE_POOL
    assert "not validated" in version.representativeness_claim
    assert version.weights_summary["segment_weights"] == {"engineering": 2.5, "support": 1.0}
    # The manifest records the same provenance for anyone reading the pool on disk.
    manifest = yaml.safe_load(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["manifest_hash"] == version.manifest_hash
    assert manifest["validity"] == "SIMULATION_ONLY"
    assert manifest["model_version"] is None
    assert len(manifest["personas"]) == 10


def test_population_snapshot_is_immutable_and_indexed(tmp_path: Path) -> None:
    store, tenant, organization, population = _seed_store("alpha")
    result = _materialize(
        store,
        _declare(store, tenant, organization, population, _segments()),
        seed=42,
        artifact_root=tmp_path,
    )
    tenant_id = tenant.id.value
    snapshots = store.list_persona_snapshots(tenant_id, result.version.id)
    assert [item.seq for item in snapshots] == list(range(1, 11))
    assert store.count_persona_snapshots(tenant_id, result.version.id) == 10

    # Re-materializing never mutates the existing version; it creates a new one.
    again = _materialize(
        store,
        store.get_population_declaration(tenant.id, population.id),
        seed=99,
        artifact_root=tmp_path / "next",
    )
    assert again.version.id != result.version.id
    unchanged = store.get_record(tenant_id, EntityKind.POPULATION_VERSION, result.version.id, PopulationVersion)
    assert unchanged.manifest_hash == result.version.manifest_hash
    assert unchanged.realized_size == 10
    assert [item.content_hash for item in store.list_persona_snapshots(tenant_id, result.version.id)] == [
        item.content_hash for item in snapshots
    ]
    versions = store.list_records(
        tenant_id, EntityKind.POPULATION_VERSION, PopulationVersion, RecordQuery(parent_id=population.id.value)
    )
    assert {item.version_number for item in versions} == {1, 2}


def test_frozen_version_still_serves_cohorts(tmp_path: Path) -> None:
    store, tenant, organization, population = _seed_store("alpha")
    result = _materialize(
        store,
        _declare(store, tenant, organization, population, _segments()),
        seed=42,
        artifact_root=tmp_path,
    )
    frozen = store.put_record(result.version.with_update(status=PopulationVersionStatus.FROZEN.value))
    cohort = build_cohort(
        store, version=frozen, name="frozen-wave", seed=3, selection=CohortSelection(size=4), repo_root=REPO_ROOT
    )
    assert cohort.size == 4


def test_cohort_is_reproducible_for_same_version_and_seed(tmp_path: Path) -> None:
    store, tenant, organization, population = _seed_store("alpha")
    result = _materialize(
        store,
        _declare(store, tenant, organization, population, _segments()),
        seed=42,
        artifact_root=tmp_path,
        segment_weights={"support": 1.0, "engineering": 2.5},
        key_dimensions=["age_bracket", "region"],
    )
    version = result.version

    first = build_cohort(
        store, version=version, name="wave-1", seed=7, selection=CohortSelection(size=5), repo_root=REPO_ROOT
    )
    repeat = build_cohort(
        store, version=version, name="wave-1-again", seed=7, selection=CohortSelection(size=5), repo_root=REPO_ROOT
    )
    other_seed = build_cohort(
        store, version=version, name="wave-2", seed=8, selection=CohortSelection(size=5), repo_root=REPO_ROOT
    )

    assert first.persona_refs == repeat.persona_refs
    assert first.content_hash == repeat.content_hash
    assert other_seed.persona_refs != first.persona_refs
    # Weights follow the personas so weighted metrics are possible downstream.
    assert set(first.weights.values()) <= {1.0, 2.5}
    assert first.provenance["manifest_hash"] == version.manifest_hash


def test_cohort_segment_shares_and_stratification_are_exact(tmp_path: Path) -> None:
    store, tenant, organization, population = _seed_store("alpha")
    result = _materialize(
        store,
        _declare(store, tenant, organization, population, _segments()),
        seed=42,
        artifact_root=tmp_path,
        key_dimensions=["age_bracket"],
    )
    version = result.version
    snapshots = {item.persona_ref: item for item in store.list_persona_snapshots(tenant.id.value, version.id)}

    balanced = build_cohort(
        store,
        version=version,
        name="balanced",
        seed=7,
        selection=CohortSelection(size=4, segment_shares={"support": 0.5, "engineering": 0.5}),
        repo_root=REPO_ROOT,
    )
    counts: dict[str, int] = {}
    for ref in balanced.persona_refs:
        segment = str(snapshots[ref].segment)
        counts[segment] = counts.get(segment, 0) + 1
    assert counts == {"support": 2, "engineering": 2}

    stratified = build_cohort(
        store,
        version=version,
        name="stratified",
        seed=5,
        selection=CohortSelection(size=6, stratify_by=["age_bracket"]),
        repo_root=REPO_ROOT,
    )
    assert stratified.size == 6
    assert stratified.selection["stratify_by"] == ["age_bracket"]
    assert len(set(stratified.persona_refs)) == 6


def test_cohort_rejects_impossible_requests(tmp_path: Path) -> None:
    store, tenant, organization, population = _seed_store("alpha")
    result = _materialize(
        store,
        _declare(store, tenant, organization, population, _segments()),
        seed=42,
        artifact_root=tmp_path,
    )
    with pytest.raises(EnterpriseSchemaError, match="exceeds"):
        build_cohort(
            store, version=result.version, name="too-big", seed=1, selection=CohortSelection(size=99), repo_root=REPO_ROOT
        )
    with pytest.raises(EnterpriseSchemaError, match="segment_shares must sum"):
        build_cohort(
            store,
            version=result.version,
            name="bad-shares",
            seed=1,
            selection=CohortSelection(size=4, segment_shares={"support": 0.7, "engineering": 0.7}),
            repo_root=REPO_ROOT,
        )
    materializing = store.put_record(
        result.version.with_update(status=PopulationVersionStatus.MATERIALIZING.value)
    )
    with pytest.raises(EnterpriseSchemaError, match="READY or FROZEN"):
        build_cohort(
            store, version=materializing, name="not-ready", seed=1, selection=CohortSelection(size=2), repo_root=REPO_ROOT
        )


def test_full_dag_backend_is_deterministic_and_honours_filters(tmp_path: Path) -> None:
    segments = [PopulationSegment(name="mid-career", count=3, filters={"age_bracket": ("35-44",)})]
    first_store, tenant, organization, population = _seed_store("alpha")
    first = materialize_population(
        first_store,
        declaration=_declare(first_store, tenant, organization, population, segments, backend="full_dag"),
        seed=11,
        artifact_root=tmp_path / "a",
        repo_root=REPO_ROOT,
    )
    second_store, tenant2, organization2, population2 = _seed_store("bravo")
    second = materialize_population(
        second_store,
        declaration=_declare(second_store, tenant2, organization2, population2, segments, backend="full_dag"),
        seed=11,
        artifact_root=tmp_path / "b",
        repo_root=REPO_ROOT,
    )
    assert first.version.manifest_hash == second.version.manifest_hash
    assert first.version.sampling_method == "full_dag"
    # Full-DAG emits the whole catalog, and the declared filter is honoured.
    for path in first.persona_paths:
        record = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        assert record["dimensions"]["age_bracket"] == "35-44"
        assert len(record["dimensions"]) > 1000
    assert first.quality.constraint_satisfaction["overall"] == 1.0


def test_hybrid_segments_may_name_different_backends(tmp_path: Path) -> None:
    segments = [
        PopulationSegment(name="grounded", count=4),
        PopulationSegment(name="synthetic", count=3, constraints={"backend": "full_dag"}),
    ]
    store, tenant, organization, population = _seed_store("alpha")
    result = _materialize(
        store,
        _declare(store, tenant, organization, population, segments),
        seed=11,
        artifact_root=tmp_path,
    )
    assert result.version.sampling_method == "full_dag+treiver"
    assert result.version.realized_size == 7
    assert {item["kind"] for item in result.version.source_datasets} == {"full_dag", "evidence_pool"}
    assert "synthetic" in result.quality.source_counts


def test_oversampling_requires_explicit_opt_in(tmp_path: Path) -> None:
    # '85+' is a valid catalog value with a single matching persona in the dev sample,
    # so a strict draw of 5 cannot be satisfied without replacement.
    segments = [PopulationSegment(name="narrow", count=5, filters={"age_bracket": ("85+",)})]
    store, tenant, organization, population = _seed_store("alpha")
    declaration = _declare(store, tenant, organization, population, segments)

    # Sampling is validated while planning, before any version record is created, so a
    # short pool fails loudly and leaves no orphan version behind.
    with pytest.raises(EnterpriseSchemaError, match="oversampling"):
        _materialize(store, declaration, seed=4, artifact_root=tmp_path / "strict")
    assert (
        store.list_records(
            tenant.id.value,
            EntityKind.POPULATION_VERSION,
            PopulationVersion,
            RecordQuery(parent_id=population.id.value),
        )
        == []
    )

    relaxed = _materialize(
        store, declaration, seed=4, artifact_root=tmp_path / "relaxed", allow_replacement=True
    )
    assert relaxed.version.realized_size == 5
    # The failed attempt consumed no version number.
    assert relaxed.version.version_number == 1
    assert relaxed.version.provenance["oversampling_with_replacement"] is True
    assert relaxed.quality.duplicate_count > 0  # replacement necessarily repeats records
