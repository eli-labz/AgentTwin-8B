"""Strongly typed enterprise entities (Phase 1+).

These models complement the Phase 0 dataclasses in
:mod:`matraix.enterprise.entities` (Tenant, Organization, Population,
EnterprisePersona, ...) which remain the canonical types for those kinds.
Everything here is persisted through the generic record store and carries the
:class:`~matraix.enterprise.domain.base.EnterpriseRecord` envelope.

Simulated users are controlled experimental instruments. Nothing in this
module asserts equivalence with real human populations; validity is carried
explicitly by :class:`ValidityClass`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from matraix.enterprise.domain.base import EnterpriseRecord
from matraix.enterprise.errors import EnterpriseSchemaError
from matraix.enterprise.ids import EntityKind

# --------------------------------------------------------------------------- #
# Shared vocabularies
# --------------------------------------------------------------------------- #


class ValidityClass(str, Enum):
    """How far a result may be trusted. Every report must display one."""

    SIMULATION_ONLY = "SIMULATION_ONLY"
    CALIBRATED_TO_EXTERNAL_DATA = "CALIBRATED_TO_EXTERNAL_DATA"
    HUMAN_VALIDATED = "HUMAN_VALIDATED"


class PrincipalKind(str, Enum):
    USER = "user"
    SERVICE_ACCOUNT = "service_account"


class RoleName(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    RESEARCHER = "researcher"
    ANALYST = "analyst"
    VIEWER = "viewer"


class ExperimentStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    ESTIMATED = "ESTIMATED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RunStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TrialStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    DEAD_LETTER = "DEAD_LETTER"


class PopulationVersionStatus(str, Enum):
    MATERIALIZING = "MATERIALIZING"
    READY = "READY"
    FAILED = "FAILED"
    FROZEN = "FROZEN"


class ApprovalDecision(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class MetricLevel(str, Enum):
    STEP = "step"
    TURN = "turn"
    TASK = "task"
    TRIAL = "trial"
    PERSONA = "persona"
    COHORT = "cohort"
    POPULATION = "population"
    EXPERIMENT = "experiment"
    PORTFOLIO = "enterprise_portfolio"


class MetricKind(str, Enum):
    PROPORTION = "proportion"
    MEAN = "mean"
    COUNT = "count"
    DURATION_MS = "duration_ms"
    TOKENS = "tokens"
    COST_USD = "cost_usd"
    DISTRIBUTION = "distribution"


class TaskType(str, Enum):
    SURVEY = "survey"
    CHATBOT = "chatbot"
    WEB = "web"
    OS_APP = "os-app"


def stable_hash(payload: Any) -> str:
    """Deterministic sha256 over a JSON-normalized payload."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require_text(value: str, *, field_name: str, max_len: int = 256) -> str:
    text = str(value or "").strip()
    if not text or len(text) > max_len:
        raise EnterpriseSchemaError(
            f"{field_name} must be a non-empty string up to {max_len} characters"
        )
    return text


# --------------------------------------------------------------------------- #
# Control layer: identity and access
# --------------------------------------------------------------------------- #


class Workspace(EnterpriseRecord):
    kind: ClassVar[EntityKind] = EntityKind.WORKSPACE
    name: str
    description: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _require_text(value, field_name="name")


class User(EnterpriseRecord):
    """Human principal known to a tenant (local or federated)."""

    kind: ClassVar[EntityKind] = EntityKind.USER
    subject: str
    display_name: str | None = None
    email: str | None = None
    roles: list[RoleName] = Field(default_factory=list)
    identity_provider: str = "local"
    external_id: str | None = None
    last_login_at: datetime | None = None

    @field_validator("subject")
    @classmethod
    def _subject(cls, value: str) -> str:
        return _require_text(value, field_name="subject")


class ServiceAccount(EnterpriseRecord):
    """Non-human principal. Only a salted token hash is stored."""

    kind: ClassVar[EntityKind] = EntityKind.SERVICE_ACCOUNT
    name: str
    roles: list[RoleName] = Field(default_factory=list)
    token_hash: str
    token_prefix: str
    expires_at: datetime | None = None
    last_used_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _require_text(value, field_name="name")

    @field_validator("token_hash")
    @classmethod
    def _hash(cls, value: str) -> str:
        text = str(value or "").strip()
        if len(text) != 64:
            raise EnterpriseSchemaError("token_hash must be a sha256 hex digest")
        return text


class Role(EnterpriseRecord):
    kind: ClassVar[EntityKind] = EntityKind.ROLE
    name: str
    permissions: list[str] = Field(default_factory=list)
    builtin: bool = False
    description: str | None = None


class RoleBinding(EnterpriseRecord):
    kind: ClassVar[EntityKind] = EntityKind.ROLE_BINDING
    principal_id: str
    principal_kind: PrincipalKind
    role: RoleName
    scope_kind: str = "tenant"
    scope_id: str | None = None

    def parent_id(self) -> str | None:
        return self.principal_id


# --------------------------------------------------------------------------- #
# Data layer: populations
# --------------------------------------------------------------------------- #


class PopulationVersion(EnterpriseRecord):
    """Immutable, materialized population snapshot.

    Records the full construction methodology so any downstream result can be
    audited back to sources, sampling method, seed, constraints, generator and
    model versions, and validation findings.
    """

    kind: ClassVar[EntityKind] = EntityKind.POPULATION_VERSION
    population_id: str
    version_number: int
    status: str = PopulationVersionStatus.MATERIALIZING.value
    declaration: dict[str, Any] = Field(default_factory=dict)
    source_datasets: list[dict[str, Any]] = Field(default_factory=list)
    schema_version: str = "1.0"
    sampling_method: str = ""
    seed: int = 0
    filters: dict[str, list[str]] = Field(default_factory=dict)
    constraints: list[dict[str, Any]] = Field(default_factory=list)
    weights_summary: dict[str, Any] = Field(default_factory=dict)
    generator_version: str = ""
    model_version: str | None = None
    validation_findings: list[dict[str, Any]] = Field(default_factory=list)
    privacy_mode: str = "aggregate_stats_then_synthetic"
    target_size: int = 0
    realized_size: int = 0
    artifact_root: str | None = None
    manifest_hash: str | None = None
    quality: dict[str, Any] = Field(default_factory=dict)
    representativeness_claim: str = (
        "none: simulated population; not validated against a real human population"
    )

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        return PopulationVersionStatus(value).value

    def parent_id(self) -> str | None:
        return self.population_id


class PersonaSnapshot(BaseModel):
    """One materialized persona inside a population version (manifest row)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    population_version_id: str
    seq: int
    persona_ref: str
    path: str
    content_hash: str
    weight: float = 1.0
    segment: str | None = None
    source: str = ""
    dimensions_summary: dict[str, str] = Field(default_factory=dict)


class Cohort(EnterpriseRecord):
    """Reproducible sub-selection of a population version."""

    kind: ClassVar[EntityKind] = EntityKind.COHORT
    name: str
    population_version_id: str
    seed: int
    size: int
    selection: dict[str, Any] = Field(default_factory=dict)
    persona_refs: list[str] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)
    content_hash: str = ""

    def parent_id(self) -> str | None:
        return self.population_version_id


class OrganizationalNode(EnterpriseRecord):
    kind: ClassVar[EntityKind] = EntityKind.ORG_NODE
    node_kind: str
    name: str
    parent_node_id: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    external_ref: str | None = None

    @field_validator("node_kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        from matraix.enterprise.graph import OrgNodeKind

        try:
            return OrgNodeKind(str(value).strip().lower()).value
        except ValueError as exc:
            raise EnterpriseSchemaError(
                f"unknown node_kind {value!r}; expected one of "
                + ", ".join(item.value for item in OrgNodeKind)
            ) from exc

    def parent_id(self) -> str | None:
        return self.parent_node_id


# --------------------------------------------------------------------------- #
# Control layer: experiments
# --------------------------------------------------------------------------- #


class SystemUnderTest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    kind: str = "application_task"
    endpoint: str | None = None
    model: str | None = None
    description: str | None = None


class TaskRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_path: str
    task_type: TaskType
    weight: float = 1.0
    agent_name: str | None = None


class ExecutionSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment: str = "host"
    compute_family: str = "local"
    execution_plane: str = "harbor"
    concurrency: int = 2
    timeout_seconds: int = 3600
    network_policy: str = "no-network"
    shard_size: int = 50

    @field_validator("concurrency", "shard_size")
    @classmethod
    def _positive(cls, value: int) -> int:
        if int(value) < 1:
            raise EnterpriseSchemaError("concurrency and shard_size must be >= 1")
        return int(value)


class BudgetSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_cost_usd: float | None = None
    max_tokens: int | None = None
    budget_id: str | None = None


class GovernanceSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_policy_id: str | None = None
    model_policy_id: str | None = None
    require_approval: bool = False
    data_classification: str = "INTERNAL"


class RetentionSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_retention_days: int = 90
    keep_raw_trajectories: bool = True


class SuccessCriterion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: str
    comparator: str = ">="
    threshold: float


class ExperimentSpec(BaseModel):
    """Complete, hashable experiment configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    hypothesis: str
    system_under_test: SystemUnderTest
    population_version_id: str
    cohort_ids: list[str] = Field(default_factory=list)
    evaluation_suite_id: str | None = None
    tasks: list[TaskRef] = Field(default_factory=list)
    persona_agent: str = "auto"
    persona_model: str = "anthropic/claude-haiku-4-5"
    sut_model: str | None = None
    execution: ExecutionSpec = Field(default_factory=ExecutionSpec)
    repetitions: int = 1
    seeds: list[int] = Field(default_factory=lambda: [42])
    budget: BudgetSpec = Field(default_factory=BudgetSpec)
    metrics: list[str] = Field(
        default_factory=lambda: [
            "task_success",
            "failure_rate",
            "latency_ms",
            "tokens_total",
            "cost_usd",
        ]
    )
    success_criteria: list[SuccessCriterion] = Field(default_factory=list)
    governance: GovernanceSpec = Field(default_factory=GovernanceSpec)
    retention: RetentionSpec = Field(default_factory=RetentionSpec)
    arms: dict[str, dict[str, Any]] = Field(default_factory=dict)
    agent_state_profile_id: str | None = None
    memory_scope: str = "ephemeral"

    @field_validator("hypothesis")
    @classmethod
    def _hypothesis(cls, value: str) -> str:
        return _require_text(value, field_name="hypothesis", max_len=4000)

    @field_validator("repetitions")
    @classmethod
    def _reps(cls, value: int) -> int:
        if int(value) < 1:
            raise EnterpriseSchemaError("repetitions must be >= 1")
        return int(value)

    @field_validator("seeds")
    @classmethod
    def _seeds(cls, value: list[int]) -> list[int]:
        if not value:
            raise EnterpriseSchemaError("at least one seed is required")
        return [int(item) for item in value]

    @field_validator("memory_scope")
    @classmethod
    def _memory_scope(cls, value: str) -> str:
        allowed = {"ephemeral", "session", "experiment", "persistent-synthetic"}
        text = str(value).strip().lower()
        if text not in allowed:
            raise EnterpriseSchemaError(
                f"memory_scope must be one of {sorted(allowed)}"
            )
        return text

    def spec_hash(self) -> str:
        return stable_hash(self.model_dump(mode="json"))


class Experiment(EnterpriseRecord):
    kind: ClassVar[EntityKind] = EntityKind.EXPERIMENT
    name: str
    status: str = ExperimentStatus.DRAFT.value
    current_version_id: str | None = None
    current_version_number: int = 0
    description: str | None = None
    last_plan: dict[str, Any] = Field(default_factory=dict)
    last_policy_decision: dict[str, Any] = Field(default_factory=dict)
    approval_id: str | None = None
    validity_class: ValidityClass = ValidityClass.SIMULATION_ONLY

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        return ExperimentStatus(value).value

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _require_text(value, field_name="name")


class ExperimentVersion(EnterpriseRecord):
    """Frozen configuration. Immutable once any run has started."""

    kind: ClassVar[EntityKind] = EntityKind.EXPERIMENT_VERSION
    experiment_id: str
    version_number: int
    spec: ExperimentSpec
    spec_hash: str = ""
    frozen: bool = False

    @model_validator(mode="after")
    def _hash(self) -> "ExperimentVersion":
        computed = self.spec.spec_hash()
        if self.spec_hash and self.spec_hash != computed:
            raise EnterpriseSchemaError("spec_hash does not match spec")
        if not self.spec_hash:
            object.__setattr__(self, "spec_hash", computed)
        return self

    def parent_id(self) -> str | None:
        return self.experiment_id


class EvaluationSuite(EnterpriseRecord):
    kind: ClassVar[EntityKind] = EntityKind.EVALUATION_SUITE
    name: str
    tasks: list[TaskRef] = Field(default_factory=list)
    description: str | None = None

    @field_validator("tasks")
    @classmethod
    def _tasks(cls, value: list[TaskRef]) -> list[TaskRef]:
        if not value:
            raise EnterpriseSchemaError("evaluation suite needs at least one task")
        return value


class Run(EnterpriseRecord):
    kind: ClassVar[EntityKind] = EntityKind.RUN
    experiment_id: str
    experiment_version_id: str
    status: str = RunStatus.QUEUED.value
    seed: int = 42
    plan: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    counters: dict[str, int] = Field(default_factory=dict)
    harbor_job_names: list[str] = Field(default_factory=list)
    cancel_requested: bool = False
    pause_requested: bool = False
    error: str | None = None
    idempotency_key: str | None = None
    validity_class: ValidityClass = ValidityClass.SIMULATION_ONLY
    trace_id: str | None = None
    spent_cost_usd: float = 0.0
    spent_tokens: int = 0

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        return RunStatus(value).value

    def parent_id(self) -> str | None:
        return self.experiment_id


class Trial(EnterpriseRecord):
    kind: ClassVar[EntityKind] = EntityKind.TRIAL
    run_id: str
    experiment_id: str
    shard_id: str | None = None
    work_item_id: str | None = None
    persona_ref: str
    persona_path: str | None = None
    task_path: str
    task_type: str = "survey"
    arm: str | None = None
    repetition: int = 0
    seed: int = 0
    status: str = TrialStatus.PENDING.value
    harbor_job_name: str | None = None
    harbor_trial_name: str | None = None
    reward: float | None = None
    passed: bool | None = None
    n_input_tokens: int | None = None
    n_output_tokens: int | None = None
    n_cache_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: float | None = None
    model_latency_ms: float | None = None
    verifier_latency_ms: float | None = None
    queue_latency_ms: float | None = None
    error_class: str | None = None
    error_message: str | None = None
    attempt: int = 1
    artifact_refs: list[str] = Field(default_factory=list)
    signals: dict[str, Any] = Field(default_factory=dict)
    judge: dict[str, Any] | None = None
    trace_id: str | None = None
    worker_id: str | None = None

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        return TrialStatus(value).value

    def parent_id(self) -> str | None:
        return self.run_id


# --------------------------------------------------------------------------- #
# Data layer: metrics, artifacts, datasets
# --------------------------------------------------------------------------- #


class MetricDefinition(EnterpriseRecord):
    kind: ClassVar[EntityKind] = EntityKind.METRIC_DEFINITION
    name: str
    metric_kind: MetricKind
    levels: list[MetricLevel] = Field(default_factory=lambda: [MetricLevel.TRIAL])
    unit: str | None = None
    higher_is_better: bool = True
    deterministic: bool = True
    description: str | None = None
    judge: dict[str, Any] | None = None


class MetricObservation(EnterpriseRecord):
    kind: ClassVar[EntityKind] = EntityKind.METRIC_OBSERVATION
    metric: str
    level: MetricLevel
    subject_id: str
    run_id: str | None = None
    experiment_id: str | None = None
    value: float
    n: int = 1
    weight: float = 1.0
    ci_low: float | None = None
    ci_high: float | None = None
    breakdown: dict[str, Any] = Field(default_factory=dict)
    judge: dict[str, Any] | None = None

    def parent_id(self) -> str | None:
        return self.run_id or self.experiment_id


class Artifact(EnterpriseRecord):
    kind: ClassVar[EntityKind] = EntityKind.ARTIFACT
    artifact_kind: str
    uri: str
    sha256: str | None = None
    size_bytes: int | None = None
    content_type: str | None = None
    run_id: str | None = None
    trial_id: str | None = None
    experiment_id: str | None = None
    data_classification: str = "INTERNAL"

    def parent_id(self) -> str | None:
        return self.run_id or self.experiment_id


class DatasetReference(EnterpriseRecord):
    kind: ClassVar[EntityKind] = EntityKind.DATASET_REFERENCE
    name: str
    dataset_kind: str
    uri: str
    schema_version: str | None = None
    checksum: str | None = None
    license: str | None = None
    grounding: str = "synthetic"


# --------------------------------------------------------------------------- #
# Control layer: models, policies, budgets, quotas, approvals
# --------------------------------------------------------------------------- #


class ModelConfiguration(EnterpriseRecord):
    kind: ClassVar[EntityKind] = EntityKind.MODEL_CONFIGURATION
    provider: str
    model: str
    display_name: str | None = None
    region: str | None = None
    residency: str | None = None
    input_price_per_1m_usd: float = 0.0
    output_price_per_1m_usd: float = 0.0
    max_context_tokens: int | None = None
    allowed_classifications: list[str] = Field(
        default_factory=lambda: ["PUBLIC", "INTERNAL"]
    )
    task_types: list[str] = Field(default_factory=list)
    enabled: bool = True

    @property
    def model_name(self) -> str:
        return f"{self.provider}/{self.model}"


class ModelPolicyRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    effect: str = "allow"
    providers: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    environments: list[str] = Field(default_factory=list)
    data_classifications: list[str] = Field(default_factory=list)
    task_types: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    max_cost_usd: float | None = None
    max_context_tokens: int | None = None
    reason: str | None = None

    @field_validator("effect")
    @classmethod
    def _effect(cls, value: str) -> str:
        text = str(value).strip().lower()
        if text not in {"allow", "deny", "require_approval"}:
            raise EnterpriseSchemaError("rule effect must be allow|deny|require_approval")
        return text


class ModelPolicy(EnterpriseRecord):
    kind: ClassVar[EntityKind] = EntityKind.MODEL_POLICY
    name: str
    environment: str = "*"
    rules: list[ModelPolicyRule] = Field(default_factory=list)
    default_effect: str = "deny"
    approved_models: list[str] = Field(default_factory=list)


class ExecutionPolicy(EnterpriseRecord):
    kind: ClassVar[EntityKind] = EntityKind.EXECUTION_POLICY
    name: str
    allowed_environments: list[str] = Field(default_factory=lambda: ["host", "docker"])
    allowed_compute_families: list[str] = Field(default_factory=lambda: ["local"])
    allowed_network_modes: list[str] = Field(default_factory=lambda: ["no-network"])
    allowed_task_types: list[str] = Field(
        default_factory=lambda: [item.value for item in TaskType]
    )
    max_concurrency: int = 32
    max_trials_per_run: int = 100_000
    max_cost_usd_without_approval: float | None = 50.0
    require_approval: bool = False
    deny_external_side_effects: bool = True
    default_decision: str = "SANDBOX_ONLY"


class Budget(EnterpriseRecord):
    kind: ClassVar[EntityKind] = EntityKind.BUDGET
    name: str
    scope_kind: str = "tenant"
    scope_id: str | None = None
    limit_usd: float
    spent_usd: float = 0.0
    reserved_usd: float = 0.0
    period: str = "total"
    hard_stop: bool = True
    currency: str = "USD"

    @property
    def available_usd(self) -> float:
        return max(0.0, self.limit_usd - self.spent_usd - self.reserved_usd)


class Quota(EnterpriseRecord):
    kind: ClassVar[EntityKind] = EntityKind.QUOTA
    max_concurrent_runs: int = 4
    max_concurrent_trials: int = 64
    max_trials_per_day: int = 100_000
    max_population_size: int = 1_000_000
    max_cost_usd_per_month: float | None = None
    provider_rate_limits: dict[str, int] = Field(default_factory=dict)


class Approval(EnterpriseRecord):
    kind: ClassVar[EntityKind] = EntityKind.APPROVAL
    subject_kind: str
    subject_id: str
    requested_by: str | None = None
    decided_by: str | None = None
    decision: ApprovalDecision = ApprovalDecision.PENDING
    reason: str | None = None
    decided_at: datetime | None = None
    expires_at: datetime | None = None
    summary: dict[str, Any] = Field(default_factory=dict)

    def parent_id(self) -> str | None:
        return self.subject_id


class Report(EnterpriseRecord):
    kind: ClassVar[EntityKind] = EntityKind.REPORT
    experiment_id: str
    run_ids: list[str] = Field(default_factory=list)
    validity_class: ValidityClass = ValidityClass.SIMULATION_ONLY
    body: dict[str, Any] = Field(default_factory=dict)
    content_hash: str = ""

    def parent_id(self) -> str | None:
        return self.experiment_id


RECORD_TYPES: dict[EntityKind, type[EnterpriseRecord]] = {
    cls.kind: cls
    for cls in (
        Workspace,
        User,
        ServiceAccount,
        Role,
        RoleBinding,
        PopulationVersion,
        Cohort,
        OrganizationalNode,
        Experiment,
        ExperimentVersion,
        EvaluationSuite,
        Run,
        Trial,
        MetricDefinition,
        MetricObservation,
        Artifact,
        DatasetReference,
        ModelConfiguration,
        ModelPolicy,
        ExecutionPolicy,
        Budget,
        Quota,
        Approval,
        Report,
    )
}
