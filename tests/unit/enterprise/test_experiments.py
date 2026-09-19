"""Enterprise experiment launch records, Harbor YAML mapping, and cost estimates."""

from __future__ import annotations

from pathlib import Path

import pytest

from matraix.enterprise import (
    CrossTenantAccessError,
    EnterpriseExperiment,
    EnterpriseSchemaError,
    ExecutionBudget,
    Experiment,
    ExperimentGovernance,
    ExperimentId,
    ExperimentKind,
    InMemoryEnterpriseStore,
    PolicyDecision,
    Population,
    PopulationId,
    SqliteEnterpriseStore,
    create_tenant_with_default_org,
    estimate_experiment_cost,
    map_experiment_to_harbor_job,
    new_id,
)
from matraix.enterprise.ids import EntityKind


def _experiment(tenant, org, **kwargs):
    defaults = dict(
        id=ExperimentId(tenant.id, new_id(EntityKind.EXPERIMENT)),
        tenant_id=tenant.id,
        organization_id=org.id,
        hypothesis="Novice users retry more often",
        objective="Measure retry rate by AI literacy",
        random_seed=42,
        task_path="application/tasks/example-survey_product-feedback",
        sample_size=10,
        metrics=("retry_rate",),
        governance=ExperimentGovernance(retention_days=30, sign_off="research-lead"),
    )
    defaults.update(kwargs)
    return Experiment(**defaults)


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path: Path):
    if request.param == "memory":
        backend = InMemoryEnterpriseStore()
    else:
        backend = SqliteEnterpriseStore(tmp_path / "exp.sqlite")
    yield backend
    backend.close()


def test_enterprise_experiment_is_experiment_alias() -> None:
    assert EnterpriseExperiment is Experiment


def test_experiment_defaults_sandbox_and_kind() -> None:
    store = InMemoryEnterpriseStore()
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    experiment = _experiment(tenant, org)
    assert experiment.default_policy is PolicyDecision.SANDBOX_ONLY
    assert experiment.kind is ExperimentKind.BASELINE
    assert experiment.governance.limitations_required is True


def test_harbor_job_mapping_is_not_harbor_job_class() -> None:
    store = InMemoryEnterpriseStore()
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    experiment = _experiment(tenant, org, random_seed=7)
    mapped = map_experiment_to_harbor_job(experiment)
    assert set(mapped) == {"harbor_job", "sidecar"}
    job = mapped["harbor_job"]
    assert job["job_name"] == f"enterprise-{experiment.id.value}"
    assert job["tasks"][0]["path"] == experiment.task_path
    assert job["n_attempts"] == 1
    assert "environment" in job
    assert mapped["sidecar"]["seed"] == 7
    assert mapped["sidecar"]["experiment_id"] == experiment.id.value
    assert mapped["sidecar"]["tenant_id"] == tenant.id.value
    assert mapped["sidecar"]["default_policy"] == "SANDBOX_ONLY"
    again = map_experiment_to_harbor_job(experiment)
    assert again["sidecar"]["seed"] == mapped["sidecar"]["seed"]
    assert again["harbor_job"] == job
    from harbor.job import Job

    assert not isinstance(job, Job)


def test_mapping_requires_task_path() -> None:
    store = InMemoryEnterpriseStore()
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    experiment = _experiment(tenant, org, task_path=None)
    with pytest.raises(EnterpriseSchemaError, match="task_path"):
        map_experiment_to_harbor_job(experiment)


def test_cost_estimate_respects_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MATRIX_MAX_COST_USD", raising=False)
    monkeypatch.setenv("MATRIX_ENTERPRISE_USD_PER_1K_TOKENS", "0.003")
    monkeypatch.setenv("MATRIX_ENTERPRISE_TOKENS_PER_TRIAL", "4000")
    store = InMemoryEnterpriseStore()
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    ok = _experiment(
        tenant,
        org,
        sample_size=10,
        execution_budget=ExecutionBudget(max_cost=1.0),
    )
    estimate = estimate_experiment_cost(ok)
    assert estimate.trial_count == 10
    assert estimate.estimated_tokens == 40_000
    assert estimate.estimated_cost_usd == pytest.approx(0.12)
    assert estimate.within_budget is True
    assert estimate.decision == "SANDBOX_ONLY"

    denied = _experiment(
        tenant,
        org,
        sample_size=10,
        execution_budget=ExecutionBudget(max_cost=0.01),
    )
    over = estimate_experiment_cost(denied)
    assert over.within_budget is False
    assert over.decision == "DENY_BUDGET"


def test_experiment_round_trip_and_isolation(store) -> None:
    alpha, alpha_org = create_tenant_with_default_org(store, name="Alpha", slug="alpha")
    bravo, _bravo_org = create_tenant_with_default_org(store, name="Bravo", slug="bravo")
    population = store.put_population(
        Population(
            id=PopulationId(alpha.id, "pop_a"),
            tenant_id=alpha.id,
            organization_id=alpha_org.id,
            name="Workforce",
            target_size=10_000,
        )
    )
    experiment = _experiment(
        alpha,
        alpha_org,
        population_ids=(population.id,),
        kind=ExperimentKind.COHORT,
        random_seed=99,
    )
    store.put_experiment(experiment)
    loaded = store.get_experiment(alpha.id, experiment.id)
    assert loaded.random_seed == 99
    assert loaded.kind is ExperimentKind.COHORT
    assert loaded.governance.sign_off == "research-lead"
    assert loaded.task_path.endswith("example-survey_product-feedback")
    assert [item.id for item in store.list_experiments(alpha.id)] == [experiment.id]
    assert store.list_experiments(bravo.id) == []
    with pytest.raises(CrossTenantAccessError):
        store.get_experiment(bravo.id, experiment.id)


def test_unknown_kind_rejected() -> None:
    store = InMemoryEnterpriseStore()
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    with pytest.raises(EnterpriseSchemaError, match="kind"):
        _experiment(tenant, org, kind="rewrite-harbor")
