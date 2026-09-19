"""Evaluation SDK — deterministic verifiers first, LLM judges supplemental.

Never presents synthetic outputs as equivalent to human research. This module
does not import LiteLLM or call a model provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from matraix.enterprise.ids import ExecutionId, ExperimentId, TenantId
from matraix.enterprise.telemetry import FailureClass, FailureRecord, classify_failure

SYNTHETIC_EVALUATION_LIMITATION = (
    "Synthetic persona outputs are simulation parameters, not equivalent to "
    "human research, usability testing, or employee consultation."
)
HUMAN_VALIDATION_NOTE = (
    "Recommended human validation before operational or research claims."
)


class EvaluatorKind(str, Enum):
    DETERMINISTIC = "deterministic"
    LLM_JUDGE = "llm_judge"


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    passed: bool
    kind: EvaluatorKind
    verifier_name: str
    score: float | None = None
    reasons: tuple[str, ...] = ()
    limitations: tuple[str, ...] = (
        SYNTHETIC_EVALUATION_LIMITATION,
        HUMAN_VALIDATION_NOTE,
    )
    recommended_human_validation: bool = True
    used_for_pass_fail: bool = True
    judge_supplemental: bool = False

    def __post_init__(self) -> None:
        kind = self.kind if isinstance(self.kind, EvaluatorKind) else EvaluatorKind(self.kind)
        object.__setattr__(self, "kind", kind)
        limits = tuple(self.limitations)
        if SYNTHETIC_EVALUATION_LIMITATION not in limits:
            limits = (SYNTHETIC_EVALUATION_LIMITATION,) + limits
        if HUMAN_VALIDATION_NOTE not in limits:
            limits = limits + (HUMAN_VALIDATION_NOTE,)
        object.__setattr__(self, "limitations", limits)
        if kind is EvaluatorKind.LLM_JUDGE:
            object.__setattr__(self, "used_for_pass_fail", False)
            object.__setattr__(self, "judge_supplemental", True)
            object.__setattr__(self, "recommended_human_validation", True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "kind": self.kind.value,
            "verifier_name": self.verifier_name,
            "score": self.score,
            "reasons": list(self.reasons),
            "limitations": list(self.limitations),
            "recommended_human_validation": self.recommended_human_validation,
            "used_for_pass_fail": self.used_for_pass_fail,
            "judge_supplemental": self.judge_supplemental,
        }


@dataclass(frozen=True, slots=True)
class EvaluationBundle:
    tenant_id: TenantId
    execution_id: ExecutionId | None
    experiment_id: ExperimentId | None
    passed: bool
    results: tuple[EvaluationResult, ...]
    failure: FailureRecord | None = None
    seed: int | None = None
    code_version: str | None = None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": str(self.tenant_id),
            "execution_id": self.execution_id.value if self.execution_id else None,
            "experiment_id": self.experiment_id.value if self.experiment_id else None,
            "passed": self.passed,
            "results": [item.to_dict() for item in self.results],
            "failure": self.failure.to_dict() if self.failure else None,
            "seed": self.seed,
            "code_version": self.code_version,
            "notes": list(self.notes),
            "limitations": [
                SYNTHETIC_EVALUATION_LIMITATION,
                HUMAN_VALIDATION_NOTE,
            ],
            "recommended_human_validation": True,
            "synthetic_equivalent_to_human_research": False,
        }


def deterministic_sandbox_verifier(
    *,
    result: dict[str, Any],
    artifacts: Iterable[dict[str, Any]] = (),
) -> EvaluationResult:
    """Pass when the sandbox path stayed a document, not a Harbor Job."""
    reasons: list[str] = []
    passed = True
    if result.get("replaced_harbor_job") is True:
        passed = False
        reasons.append("sandbox path must not replace harbor.Job")
    if result.get("denied") is True:
        passed = False
        reasons.append("policy denied the execution")
    if result.get("held_for_approval") is True:
        passed = False
        reasons.append("execution held for approval")
    if result.get("available") is False:
        passed = False
        reasons.append("worker unavailable")
    kinds = {str(item.get("kind") or "") for item in artifacts}
    if result.get("sandbox") and "completion" not in kinds and result.get("denied") is not True:
        if result.get("held_for_approval") is not True and result.get("available") is not False:
            passed = False
            reasons.append("sandbox completion artifact missing")
    harbor = next((item for item in artifacts if item.get("kind") == "harbor_job"), None)
    if harbor is not None:
        content = harbor.get("content") or {}
        job = content.get("harbor_job")
        if not isinstance(job, dict):
            passed = False
            reasons.append("harbor_job artifact is not a document")
    if passed:
        reasons.append("deterministic sandbox checks passed")
    return EvaluationResult(
        passed=passed,
        kind=EvaluatorKind.DETERMINISTIC,
        verifier_name="sandbox_path",
        score=1.0 if passed else 0.0,
        reasons=tuple(reasons),
        used_for_pass_fail=True,
    )


def supplemental_llm_judge(*, invoked: bool = False) -> EvaluationResult:
    """LLM judges are recorded only as supplemental notes. No provider call."""
    if invoked:
        reasons = (
            "LLM judge requested but not invoked — no provider SDK in the evaluation SDK",
        )
    else:
        reasons = ("LLM judge not requested; deterministic verifier is authoritative",)
    return EvaluationResult(
        passed=True,
        kind=EvaluatorKind.LLM_JUDGE,
        verifier_name="supplemental_llm_judge",
        score=None,
        reasons=reasons,
        used_for_pass_fail=False,
        judge_supplemental=True,
    )


def evaluate_execution(
    *,
    tenant_id: TenantId,
    execution_id: ExecutionId | None,
    experiment_id: ExperimentId | None,
    result: dict[str, Any],
    artifacts: Iterable[dict[str, Any]] = (),
    status: str | None = None,
    reasons: Iterable[str] = (),
    seed: int | None = None,
    code_version: str | None = None,
    include_llm_judge: bool = False,
) -> EvaluationBundle:
    """Compose deterministic (authoritative) and optional supplemental judge."""
    from matraix.enterprise.telemetry import resolve_code_version

    deterministic = deterministic_sandbox_verifier(result=result, artifacts=artifacts)
    results = [deterministic]
    if include_llm_judge:
        results.append(supplemental_llm_judge(invoked=True))
    passed = deterministic.passed
    failure = None
    status_text = (status or "").lower()
    reason_text = " ".join(reasons)
    if not passed or status_text in {"denied", "held", "unavailable", "failed"}:
        if status_text == "denied" or result.get("denied"):
            classification = FailureClass.POLICY_FAILURE
            message = reason_text or "policy denied the execution"
        elif status_text == "held":
            classification = FailureClass.ESCALATION_FAILURE
            message = reason_text or "execution held for approval"
        elif status_text == "unavailable":
            classification = FailureClass.ENVIRONMENT_FAILURE
            message = reason_text or "worker unavailable"
        elif not passed:
            classification = classify_failure(reason_text or "verification failed")
            if classification is FailureClass.EXECUTION_FAILURE:
                classification = FailureClass.VERIFICATION_FAILURE
            message = reason_text or "deterministic verifier failed"
        else:
            classification = classify_failure(reason_text or status_text)
            message = reason_text or status_text
        failure = FailureRecord(
            classification=classification,
            message=message,
            tenant_id=tenant_id,
            execution_id=execution_id,
            experiment_id=experiment_id,
            details={"status": status, "result": dict(result)},
        )
    notes = (
        SYNTHETIC_EVALUATION_LIMITATION,
        HUMAN_VALIDATION_NOTE,
        "LLM judges are supplemental and never the pass/fail authority.",
    )
    return EvaluationBundle(
        tenant_id=tenant_id,
        execution_id=execution_id,
        experiment_id=experiment_id,
        passed=passed,
        results=tuple(results),
        failure=failure,
        seed=seed,
        code_version=code_version or resolve_code_version(),
        notes=notes,
    )
