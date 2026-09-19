"""Control / data / execution planes and the local sandbox worker."""

from __future__ import annotations

from pathlib import Path

import pytest

from matraix.enterprise import (
    CrossTenantAccessError,
    EnterpriseSchemaError,
    EnterpriseRuntime,
    ExecutionStatus,
    Experiment,
    ExperimentId,
    InMemoryEnterpriseStore,
    ModelPolicy,
    PlaneName,
    PolicyDecision,
    SqliteEnterpriseStore,
    WorkerKind,
    WorkerNotAvailableError,
    WorkRequest,
    WorldState,
    create_tenant_with_default_org,
    get_worker,
    new_id,
    worker_catalog,
)
from matraix.enterprise.ids import EntityKind
from matraix.enterprise.runtime import RemoteWorkerStub


def _experiment(tenant, org, **kwargs):
    defaults = dict(
        id=ExperimentId(tenant.id, new_id(EntityKind.EXPERIMENT)),
        tenant_id=tenant.id,
        organization_id=org.id,
        hypothesis="Novice users retry more often",
        objective="Measure retry rate",
        random_seed=42,
        task_path="application/tasks/example-survey_product-feedback",
        sample_size=3,
        n_attempts=1,
    )
    defaults.update(kwargs)
    return Experiment(**defaults)


def test_runtime_modules_do_not_import_harbor_or_sdks() -> None:
    import matraix.enterprise.runtime as runtime
    import matraix.enterprise.events as events

    for module in (runtime, events):
        assert "harbor" not in module.__dict__
        assert "litellm" not in module.__dict__
        assert "docker" not in module.__dict__
        assert "kubernetes" not in module.__dict__


def test_worker_catalog_local_available_others_stubbed() -> None:
    rows = {item["kind"]: item for item in worker_catalog()}
    assert rows["local"]["available"] is True
    assert rows["local"]["sandbox_only"] is True
    for kind in ("docker", "kubernetes", "queue", "batch"):
        assert rows[kind]["available"] is False


def test_local_worker_executes_sandbox_path_without_harbor_job() -> None:
    store = InMemoryEnterpriseStore()
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    experiment = store.put_experiment(_experiment(tenant, org))
    runtime = EnterpriseRuntime(store)
    record = runtime.execute_experiment(tenant.id, experiment.id)

    assert record.status is ExecutionStatus.COMPLETED
    assert record.decision is PolicyDecision.SANDBOX_ONLY
    assert record.worker_kind is WorkerKind.LOCAL
    assert record.plane is PlaneName.EXECUTION
    assert record.harbor_job_name == f"enterprise-{experiment.id.value}"
    assert record.result["sandbox"] is True
    assert record.result["replaced_harbor_job"] is False
    assert record.result["world_state_enabled"] is False
    assert record.trial_slots == 3
    assert record.concurrency == 1

    artifacts = runtime.data.list_artifacts(tenant.id, execution_id=record.id)
    kinds = {item.kind for item in artifacts}
    assert "harbor_job" in kinds
    assert "completion" in kinds
    harbor = next(item for item in artifacts if item.kind == "harbor_job")
    assert "harbor.Job" not in str(harbor.content)
    assert harbor.content["harbor_job"]["job_name"] == record.harbor_job_name
    assert isinstance(harbor.content["harbor_job"], dict)

    events = runtime.data.list_events(tenant.id, execution_id=record.id)
    kinds = {item.kind.value for item in events}
    assert "execution_submitted" in kinds
    assert "policy_evaluated" in kinds
    assert "trial_scheduled" in kinds
    assert "execution_completed" in kinds
    assert runtime.world.is_enabled(tenant.id) is False


def test_same_experiment_accepted_by_local_and_remote_stub() -> None:
    store = InMemoryEnterpriseStore()
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    experiment = store.put_experiment(_experiment(tenant, org))
    runtime = EnterpriseRuntime(store)
    local = runtime.execution.submit(
        WorkRequest(experiment=experiment, worker_kind=WorkerKind.LOCAL)
    )
    remote = runtime.execution.submit(
        WorkRequest(experiment=experiment, worker_kind=WorkerKind.KUBERNETES)
    )
    assert type(local.experiment_id) is type(remote.experiment_id)
    assert local.experiment_id == remote.experiment_id == experiment.id
    assert remote.status is ExecutionStatus.UNAVAILABLE
    assert remote.worker_kind is WorkerKind.KUBERNETES
    assert remote.decision is PolicyDecision.SANDBOX_ONLY
    with pytest.raises(WorkerNotAvailableError, match="kubernetes"):
        RemoteWorkerStub(WorkerKind.KUBERNETES).require()


def test_denied_execution_is_persisted() -> None:
    store = InMemoryEnterpriseStore()
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    store.put_model_policy(
        ModelPolicy(tenant_id=tenant.id, denied_actions=("execute_experiment",))
    )
    experiment = store.put_experiment(_experiment(tenant, org))
    record = EnterpriseRuntime(store).execute_experiment(tenant.id, experiment.id)
    assert record.status is ExecutionStatus.DENIED
    assert record.decision is PolicyDecision.DENY
    events = store.list_events(tenant.id, execution_id=record.id)
    assert any(item.kind.value == "execution_denied" for item in events)


def test_world_state_stays_off_unless_enabled() -> None:
    world = WorldState()
    store = InMemoryEnterpriseStore()
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    with pytest.raises(EnterpriseSchemaError, match="world state is disabled"):
        world.set(tenant.id, "memory", "nope")
    assert world.snapshot(tenant.id) == {}


def test_execution_isolation_across_tenants() -> None:
    store = InMemoryEnterpriseStore()
    alpha, alpha_org = create_tenant_with_default_org(store, name="Alpha", slug="alpha")
    bravo, _ = create_tenant_with_default_org(store, name="Bravo", slug="bravo")
    experiment = store.put_experiment(_experiment(alpha, alpha_org))
    runtime = EnterpriseRuntime(store)
    record = runtime.execute_experiment(alpha.id, experiment.id)
    assert runtime.execution.list(bravo.id) == []
    with pytest.raises(CrossTenantAccessError):
        runtime.execution.get(bravo.id, record.id)
    assert runtime.data.list_artifacts(bravo.id) == []
    assert runtime.data.list_events(bravo.id) == []


def test_sqlite_execution_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite"
    store = SqliteEnterpriseStore(path)
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    experiment = store.put_experiment(_experiment(tenant, org, sample_size=2))
    record = EnterpriseRuntime(store).execute_experiment(tenant.id, experiment.id)
    store.close()

    reopened = SqliteEnterpriseStore(path)
    loaded = reopened.get_execution(tenant.id, record.id)
    assert loaded.status is ExecutionStatus.COMPLETED
    assert loaded.harbor_job_name == record.harbor_job_name
    assert loaded.decision is PolicyDecision.SANDBOX_ONLY
    artifacts = reopened.list_artifacts(tenant.id, execution_id=record.id)
    assert artifacts
    events = reopened.list_events(tenant.id, execution_id=record.id)
    assert events
    reopened.close()


def test_get_worker_defaults_to_local() -> None:
    assert get_worker().kind is WorkerKind.LOCAL
    assert get_worker("docker").kind is WorkerKind.DOCKER


def test_sandbox_path_works_without_task_path() -> None:
    store = InMemoryEnterpriseStore()
    tenant, org = create_tenant_with_default_org(store, name="Acme", slug="acme")
    experiment = store.put_experiment(
        _experiment(tenant, org, task_path=None, sample_size=1)
    )
    record = EnterpriseRuntime(store).execute_experiment(tenant.id, experiment.id)
    assert record.status is ExecutionStatus.COMPLETED
    assert record.harbor_job_name is None
    kinds = {
        item.kind
        for item in store.list_artifacts(tenant.id, execution_id=record.id)
    }
    assert kinds >= {"completion", "trace", "metrics", "evaluation"}
