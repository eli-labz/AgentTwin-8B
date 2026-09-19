"""Map an enterprise experiment onto Harbor job YAML and estimate cost.

This module does **not** import or instantiate ``harbor.Job``. It produces the
same job-document shape ``matraix run -c`` already consumes. Launch is not
performed here; default policy remains ``SANDBOX_ONLY``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from matraix.enterprise.entities import (
    ExecutionBudget,
    Experiment,
    ExperimentGovernance,
    ExperimentKind,
)
from matraix.enterprise.errors import EnterpriseSchemaError
from matraix.enterprise.ids import ExperimentId, OrganizationId, PopulationId, TenantId
from matraix.enterprise.policy import DataClassification, PolicyDecision

USD_PER_1K_ENV = "MATRIX_ENTERPRISE_USD_PER_1K_TOKENS"
TOKENS_PER_TRIAL_ENV = "MATRIX_ENTERPRISE_TOKENS_PER_TRIAL"
DEFAULT_USD_PER_1K = 0.003
DEFAULT_TOKENS_PER_TRIAL = 4000


def apply_launch_payload(experiment: Experiment, payload: Mapping[str, Any] | None) -> Experiment:
    """Rebuild an experiment with optional ``launch_json`` fields."""
    data = dict(payload or {})
    governance_raw = data.get("governance") or {}
    budget = experiment.execution_budget
    return Experiment(
        id=experiment.id,
        tenant_id=experiment.tenant_id,
        organization_id=experiment.organization_id,
        hypothesis=experiment.hypothesis,
        objective=experiment.objective,
        population_ids=experiment.population_ids,
        random_seed=experiment.random_seed,
        data_classification=experiment.data_classification,
        default_policy=experiment.default_policy,
        execution_budget=budget,
        variables=experiment.variables,
        kind=data.get("kind", experiment.kind),
        task_path=data.get("task_path", experiment.task_path),
        model_name=data.get("model_name", experiment.model_name),
        agent_name=data.get("agent_name", experiment.agent_name),
        metrics=tuple(data.get("metrics") or experiment.metrics),
        governance=ExperimentGovernance(
            retention_days=governance_raw.get(
                "retention_days", experiment.governance.retention_days
            ),
            requires_human_validation=governance_raw.get(
                "requires_human_validation",
                experiment.governance.requires_human_validation,
            ),
            sign_off=governance_raw.get("sign_off", experiment.governance.sign_off),
            notes=governance_raw.get("notes", experiment.governance.notes),
            limitations_required=governance_raw.get(
                "limitations_required", experiment.governance.limitations_required
            ),
        ),
        sample_size=data.get("sample_size", experiment.sample_size),
        n_attempts=int(data.get("n_attempts", experiment.n_attempts)),
        trial_profile=str(data.get("trial_profile") or experiment.trial_profile),
        execution_mode=str(data.get("execution_mode") or experiment.execution_mode),
    )


def resolve_trial_count(
    experiment: Experiment, *, population_target: int | None = None
) -> int:
    if experiment.sample_size is not None:
        base = experiment.sample_size
    elif population_target is not None:
        base = int(population_target)
    else:
        base = 1
    return max(1, base) * experiment.n_attempts


def _rate_from_env() -> tuple[float, int]:
    usd_raw = (os.environ.get(USD_PER_1K_ENV) or "").strip()
    tok_raw = (os.environ.get(TOKENS_PER_TRIAL_ENV) or "").strip()
    usd = float(usd_raw) if usd_raw else DEFAULT_USD_PER_1K
    tokens = int(tok_raw) if tok_raw else DEFAULT_TOKENS_PER_TRIAL
    if usd < 0 or tokens < 1:
        raise EnterpriseSchemaError("cost estimate rates must be non-negative")
    return usd, tokens


@dataclass(frozen=True, slots=True)
class CostEstimate:
    experiment_id: str
    tenant_id: str
    trial_count: int
    estimated_tokens: int
    estimated_cost_usd: float
    usd_per_1k_tokens: float
    tokens_per_trial: int
    budget_max_cost: float | None
    env_max_cost: float | None
    within_budget: bool
    decision: str
    default_policy: str
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "tenant_id": self.tenant_id,
            "trial_count": self.trial_count,
            "estimated_tokens": self.estimated_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "usd_per_1k_tokens": self.usd_per_1k_tokens,
            "tokens_per_trial": self.tokens_per_trial,
            "budget_max_cost": self.budget_max_cost,
            "env_max_cost": self.env_max_cost,
            "within_budget": self.within_budget,
            "decision": self.decision,
            "default_policy": self.default_policy,
            "notes": list(self.notes),
        }


def estimate_experiment_cost(
    experiment: Experiment,
    *,
    population_target: int | None = None,
) -> CostEstimate:
    """Pre-run cost estimate. Does not launch Harbor or change ``matraix run``."""
    usd_per_1k, tokens_per_trial = _rate_from_env()
    trial_count = resolve_trial_count(
        experiment, population_target=population_target
    )
    estimated_tokens = trial_count * tokens_per_trial
    estimated_cost = round((estimated_tokens / 1000.0) * usd_per_1k, 6)
    env_raw = (os.environ.get("MATRIX_MAX_COST_USD") or "").strip()
    env_max = float(env_raw) if env_raw else None
    budget_max = experiment.execution_budget.max_cost
    notes = [
        "Estimate only — no Harbor job is started.",
        "Synthetic personas are simulation parameters, not human equivalents.",
        f"Default policy remains {experiment.default_policy.value}.",
    ]
    over_budget = False
    if budget_max is not None and estimated_cost > float(budget_max):
        over_budget = True
        notes.append(
            f"Estimated ${estimated_cost:g} exceeds experiment budget ${budget_max:g}."
        )
    if env_max is not None and estimated_cost > env_max:
        over_budget = True
        notes.append(
            f"Estimated ${estimated_cost:g} exceeds MATRIX_MAX_COST_USD=${env_max:g}."
        )
    if experiment.execution_budget.max_tokens is not None:
        if estimated_tokens > experiment.execution_budget.max_tokens:
            over_budget = True
            notes.append(
                f"Estimated {estimated_tokens} tokens exceeds max_tokens="
                f"{experiment.execution_budget.max_tokens}."
            )
    decision = "DENY_BUDGET" if over_budget else experiment.default_policy.value
    return CostEstimate(
        experiment_id=experiment.id.value,
        tenant_id=experiment.tenant_id.value,
        trial_count=trial_count,
        estimated_tokens=estimated_tokens,
        estimated_cost_usd=estimated_cost,
        usd_per_1k_tokens=usd_per_1k,
        tokens_per_trial=tokens_per_trial,
        budget_max_cost=budget_max,
        env_max_cost=env_max,
        within_budget=not over_budget,
        decision=decision,
        default_policy=experiment.default_policy.value,
        notes=tuple(notes),
    )


def map_experiment_to_harbor_job(
    experiment: Experiment,
    *,
    persona_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Return a Harbor job document plus sidecar metadata.

    The ``harbor_job`` object matches generated application-job YAML. It is
    not a ``harbor.Job`` instance.
    """
    if not experiment.task_path:
        raise EnterpriseSchemaError(
            "task_path is required to map an experiment onto a Harbor job"
        )
    seed = 42 if experiment.random_seed is None else experiment.random_seed
    model_name = experiment.model_name or "anthropic/claude-sonnet-4-6"
    agent_name = experiment.agent_name or (
        "persona-json-survey"
        if experiment.trial_profile == "json_survey"
        else "persona-claude-code"
    )
    paths = list(persona_paths or [])
    if not paths:
        paths = [""]
    agents = []
    for path in paths:
        spec: dict[str, Any] = {
            "name": agent_name,
            "model_name": model_name,
        }
        if path:
            spec["kwargs"] = {"persona_path": path}
        agents.append(spec)
    environment = (
        {"type": "host", "delete": True}
        if experiment.trial_profile in {"json_survey", "user_sim_chat"}
        else {"type": "docker", "delete": True}
    )
    concurrency = experiment.execution_budget.max_concurrency or 1
    job_name = f"enterprise-{experiment.id.value}"
    harbor_job = {
        "job_name": job_name,
        "jobs_dir": "jobs",
        "n_attempts": experiment.n_attempts,
        "timeout_multiplier": 1.0,
        "n_concurrent_trials": concurrency,
        "quiet": False,
        "environment": environment,
        "agents": agents,
        "tasks": [{"path": experiment.task_path}],
    }
    sidecar = {
        "experiment_id": experiment.id.value,
        "tenant_id": experiment.tenant_id.value,
        "seed": seed,
        "kind": experiment.kind.value,
        "default_policy": experiment.default_policy.value,
        "data_classification": experiment.data_classification.value,
        "hypothesis": experiment.hypothesis,
        "objective": experiment.objective,
        "metrics": list(experiment.metrics),
        "governance": experiment.governance.to_dict(),
        "sample_size": experiment.sample_size or max(1, len([p for p in paths if p])),
        "task": experiment.task_path,
        "model_name": model_name,
        "agent_name": agent_name,
        "execution_mode": experiment.execution_mode,
        "trial_profile": experiment.trial_profile,
    }
    return {"harbor_job": harbor_job, "sidecar": sidecar}


def experiment_from_parts(
    *,
    tenant_id: TenantId,
    organization_id: OrganizationId,
    experiment_id: ExperimentId | None = None,
    hypothesis: str,
    objective: str,
    population_ids: list[PopulationId] | tuple[PopulationId, ...] = (),
    random_seed: int | None = None,
    data_classification: str | DataClassification = DataClassification.INTERNAL,
    default_policy: str | PolicyDecision = PolicyDecision.SANDBOX_ONLY,
    execution_budget: ExecutionBudget | None = None,
    variables: dict[str, str] | None = None,
    kind: str | ExperimentKind = ExperimentKind.BASELINE,
    task_path: str | None = None,
    model_name: str | None = None,
    agent_name: str | None = None,
    metrics: list[str] | tuple[str, ...] = (),
    governance: ExperimentGovernance | None = None,
    sample_size: int | None = None,
    n_attempts: int = 1,
    trial_profile: str = "json_survey",
    execution_mode: str = "auto",
) -> Experiment:
    from matraix.enterprise.ids import EntityKind, new_id

    return Experiment(
        id=experiment_id or ExperimentId(tenant_id, new_id(EntityKind.EXPERIMENT)),
        tenant_id=tenant_id,
        organization_id=organization_id,
        hypothesis=hypothesis,
        objective=objective,
        population_ids=tuple(population_ids),
        random_seed=random_seed,
        data_classification=data_classification,
        default_policy=default_policy,
        execution_budget=execution_budget or ExecutionBudget(),
        variables=variables or {},
        kind=kind,
        task_path=task_path,
        model_name=model_name,
        agent_name=agent_name,
        metrics=tuple(metrics),
        governance=governance or ExperimentGovernance(),
        sample_size=sample_size,
        n_attempts=n_attempts,
        trial_profile=trial_profile,
        execution_mode=execution_mode,
    )
