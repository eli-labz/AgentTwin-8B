"""Population quality analysis.

Scores a materialized population against its declaration. Every metric is
computed from the records themselves, deterministically, with no model call.

Metrics (brief section D):

* ``coverage`` — how much of the persona schema is actually populated
* ``distribution_deviation`` — observed vs declared segment/marginal shares
* ``constraint_satisfaction`` — do records honour their segment's filters
* ``duplicate_rate`` — identical dimension vectors
* ``missingness`` — per-dimension gaps (worst offenders, bounded)
* ``contradiction_rate`` — cross-dimension consistency via the existing
  :func:`matraix.persona_consistency.validate_dimensions`. **Non-zero rates are
  expected**: this is a measured property of the upstream persona sources, not of
  materialization. Measured on this repository at the 2026-09-23 baseline, 32% of
  the checked-in dev-sample personas (64/200) and 52% of Full-DAG-sampled personas
  (5,239/10,000) already fail this check, because ``generate_persona_pool`` stamps
  assignments without re-sampling on a consistency violation. The metric is therefore reported
  as a warning with that baseline attached, so a reader can distinguish "worse than
  the corpus" from "typical of the corpus".
* ``effective_sample_size`` — Kish ESS when weights vary
* ``subgroup_counts`` / ``rare_groups`` — subgroup and rare-group coverage

Representativeness is **never** inferred from these numbers. The report states
a claim only when the inputs justify one; the default is simulation-only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from matraix.persona_consistency import validate_dimensions

__all__ = [
    "CORPUS_CONTRADICTION_BASELINE",
    "QualityReport",
    "RARE_GROUP_THRESHOLD",
    "analyze_population_quality",
]

RARE_GROUP_THRESHOLD = 30
#: Cross-dimension contradiction rates measured on this repository's own persona
#: sources (2026-09-23). Used only to contextualize a report, never to excuse it.
CORPUS_CONTRADICTION_BASELINE = {
    # Full census of the checked-in pool: 64 of 200 personas.
    "matraix-persona-dev-sample": 0.32,
    "matraix-persona-dev-sample_n": 200,
    # Measured over a 10,000-persona Full-DAG materialization (5,239 of 10,000).
    # An earlier 30-row spot check read 0.43; the large sample supersedes it.
    "full_dag_sample": 0.52,
    "full_dag_sample_n": 10_000,
    "measured_at": "2026-09-23",
    "validator": "matraix.persona_consistency.validate_dimensions",
}
_MAX_REPORTED_DIMENSIONS = 25
_MAX_REPORTED_SUBGROUPS = 50


@dataclass(frozen=True)
class QualityReport:
    """Deterministic quality summary for one materialized population."""

    total: int
    realized_size: int
    target_size: int
    coverage: dict[str, float]
    missingness: dict[str, float]
    duplicate_rate: float
    duplicate_count: int
    contradiction_rate: float
    contradictions: list[dict[str, Any]]
    constraint_satisfaction: dict[str, Any]
    distribution_deviation: dict[str, Any]
    segment_counts: dict[str, int]
    subgroup_counts: dict[str, dict[str, int]]
    rare_groups: list[dict[str, Any]]
    effective_sample_size: float
    weights_vary: bool
    source_counts: dict[str, int]
    contradiction_baseline: dict[str, Any] = field(default_factory=lambda: dict(CORPUS_CONTRADICTION_BASELINE))
    findings: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no finding is an error.

        Contradictions and rare subgroups do not clear this flag: both are
        measured properties of the inputs. Constraint violations, an empty
        population, and materialization failures do.
        """
        return not any(item.get("severity") == "error" for item in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "realized_size": self.realized_size,
            "target_size": self.target_size,
            "coverage": self.coverage,
            "missingness": self.missingness,
            "duplicate_rate": self.duplicate_rate,
            "duplicate_count": self.duplicate_count,
            "contradiction_rate": self.contradiction_rate,
            "contradiction_baseline": self.contradiction_baseline,
            "contradictions": self.contradictions,
            "constraint_satisfaction": self.constraint_satisfaction,
            "distribution_deviation": self.distribution_deviation,
            "segment_counts": self.segment_counts,
            "subgroup_counts": self.subgroup_counts,
            "rare_groups": self.rare_groups,
            "effective_sample_size": self.effective_sample_size,
            "weights_vary": self.weights_vary,
            "source_counts": self.source_counts,
            "findings": self.findings,
            "ok": self.ok,
        }

    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _round(value: float, places: int = 6) -> float:
    return round(float(value), places)


def _share_map(counts: Mapping[str, int], total: int) -> dict[str, float]:
    if total <= 0:
        return {}
    return {key: _round(value / total) for key, value in sorted(counts.items())}


def analyze_population_quality(
    records: Sequence[Mapping[str, Any]],
    *,
    target_size: int,
    catalog_dimension_ids: Iterable[str] = (),
    segments: Sequence[Mapping[str, Any]] = (),
    key_dimensions: Sequence[str] = (),
    rare_group_threshold: int = RARE_GROUP_THRESHOLD,
) -> QualityReport:
    """Analyze materialized ``records``.

    Each record is a mapping with ``dimensions`` and optionally ``source``,
    ``segment`` and ``weight``. ``segments`` are declaration segments carrying
    ``name``, ``resolved_count`` and ``filters``.
    """
    rows = list(records)
    total = len(rows)
    catalog = sorted({str(item) for item in catalog_dimension_ids})
    findings: list[dict[str, str]] = []

    # ---- coverage / missingness ---------------------------------------- #
    present_counts: dict[str, int] = {}
    per_record_filled: list[int] = []
    for row in rows:
        dimensions = row.get("dimensions") or {}
        filled = 0
        for key, value in dimensions.items():
            if value is None or str(value).strip() == "":
                continue
            filled += 1
            name = str(key)
            present_counts[name] = present_counts.get(name, 0) + 1
        per_record_filled.append(filled)

    observed_dimension_ids = sorted(present_counts)
    catalog_size = len(catalog) if catalog else len(observed_dimension_ids)
    mean_filled = (sum(per_record_filled) / total) if total else 0.0
    coverage = {
        "catalog_dimension_count": float(catalog_size),
        "observed_dimension_count": float(len(observed_dimension_ids)),
        "mean_dimensions_per_persona": _round(mean_filled),
        "mean_fill_ratio": _round(mean_filled / catalog_size) if catalog_size else 0.0,
        "dimension_coverage": _round(len(observed_dimension_ids) / catalog_size) if catalog_size else 0.0,
    }

    missingness: dict[str, float] = {}
    if total:
        checked = catalog or observed_dimension_ids
        gaps = {
            name: _round(1.0 - (present_counts.get(name, 0) / total))
            for name in checked
        }
        worst = sorted(gaps.items(), key=lambda item: (-item[1], item[0]))
        missingness = {name: value for name, value in worst[:_MAX_REPORTED_DIMENSIONS] if value > 0}

    # ---- duplicates ---------------------------------------------------- #
    seen: set[str] = set()
    duplicate_count = 0
    for row in rows:
        fingerprint = json.dumps(row.get("dimensions") or {}, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()
        if digest in seen:
            duplicate_count += 1
        else:
            seen.add(digest)
    duplicate_rate = _round(duplicate_count / total) if total else 0.0

    # ---- contradictions (reuses the existing consistency validator) ----- #
    contradictions: list[dict[str, Any]] = []
    contradiction_hits = 0
    for row in rows:
        errors = validate_dimensions(dict(row.get("dimensions") or {}))
        if errors:
            contradiction_hits += 1
            if len(contradictions) < 20:
                contradictions.append(
                    {"persona_ref": str(row.get("persona_id") or row.get("persona_ref") or ""), "errors": list(errors)}
                )
    contradiction_rate = _round(contradiction_hits / total) if total else 0.0

    # ---- segments: counts, constraint satisfaction, deviation ----------- #
    segment_counts: dict[str, int] = {}
    for row in rows:
        name = str(row.get("segment") or "")
        if name:
            segment_counts[name] = segment_counts.get(name, 0) + 1

    constraint_satisfaction: dict[str, Any] = {"segments": {}, "overall": 1.0}
    satisfied_total = 0
    constrained_total = 0
    declared_counts: dict[str, int] = {}
    for segment in segments:
        name = str(segment.get("name") or "")
        filters = {str(key): [str(item) for item in values] for key, values in (segment.get("filters") or {}).items()}
        declared = int(segment.get("resolved_count") or segment.get("count") or 0)
        if name:
            declared_counts[name] = declared
        members = [row for row in rows if str(row.get("segment") or "") == name]
        if not filters:
            constraint_satisfaction["segments"][name] = {
                "declared_count": declared,
                "realized_count": len(members),
                "filters": {},
                "satisfied_ratio": 1.0,
            }
            continue
        per_filter: dict[str, float] = {}
        segment_satisfied = 0
        for row in members:
            dimensions = row.get("dimensions") or {}
            if all(str(dimensions.get(dim, "")) in allowed for dim, allowed in filters.items()):
                segment_satisfied += 1
        for dim, allowed in sorted(filters.items()):
            hits = sum(1 for row in members if str((row.get("dimensions") or {}).get(dim, "")) in allowed)
            per_filter[dim] = _round(hits / len(members)) if members else 0.0
        constrained_total += len(members)
        satisfied_total += segment_satisfied
        ratio = _round(segment_satisfied / len(members)) if members else 0.0
        constraint_satisfaction["segments"][name] = {
            "declared_count": declared,
            "realized_count": len(members),
            "filters": per_filter,
            "satisfied_ratio": ratio,
        }
        if members and ratio < 1.0:
            findings.append(
                {
                    "severity": "error",
                    "code": "constraint_violation",
                    "message": f"segment {name!r}: {len(members) - segment_satisfied} record(s) violate declared filters",
                }
            )
    constraint_satisfaction["overall"] = _round(satisfied_total / constrained_total) if constrained_total else 1.0

    observed_segment_shares = _share_map(segment_counts, total)
    declared_total = sum(declared_counts.values())
    declared_segment_shares = _share_map(declared_counts, declared_total) if declared_total else {}
    segment_tvd = 0.0
    per_segment_delta: dict[str, float] = {}
    for name in sorted(set(observed_segment_shares) | set(declared_segment_shares)):
        observed = observed_segment_shares.get(name, 0.0)
        declared_share = declared_segment_shares.get(name, 0.0)
        per_segment_delta[name] = _round(observed - declared_share)
        segment_tvd += abs(observed - declared_share)
    distribution_deviation: dict[str, Any] = {
        "segment_shares_observed": observed_segment_shares,
        "segment_shares_declared": declared_segment_shares,
        "segment_share_delta": per_segment_delta,
        "segment_total_variation_distance": _round(segment_tvd / 2.0),
    }

    for name, declared in sorted(declared_counts.items()):
        realized = segment_counts.get(name, 0)
        if declared and realized != declared:
            findings.append(
                {
                    "severity": "warning",
                    "code": "segment_count_mismatch",
                    "message": f"segment {name!r}: declared {declared}, realized {realized}",
                }
            )

    # ---- subgroups and rare groups -------------------------------------- #
    wanted_dimensions = [str(item) for item in key_dimensions]
    if not wanted_dimensions:
        declared_dims = sorted(
            {str(key) for segment in segments for key in (segment.get("filters") or {})}
        )
        wanted_dimensions = declared_dims
    subgroup_counts: dict[str, dict[str, int]] = {}
    for dimension in wanted_dimensions[:_MAX_REPORTED_DIMENSIONS]:
        counts: dict[str, int] = {}
        for row in rows:
            value = str((row.get("dimensions") or {}).get(dimension, "")).strip()
            if value:
                counts[value] = counts.get(value, 0) + 1
        if counts:
            subgroup_counts[dimension] = dict(sorted(counts.items()))

    rare_groups: list[dict[str, Any]] = []
    for dimension, counts in sorted(subgroup_counts.items()):
        for value, count in sorted(counts.items()):
            if count < rare_group_threshold:
                rare_groups.append({"dimension": dimension, "value": value, "count": count})
    rare_groups = rare_groups[:_MAX_REPORTED_SUBGROUPS]
    if rare_groups:
        findings.append(
            {
                "severity": "info",
                "code": "rare_subgroups",
                "message": (
                    f"{len(rare_groups)} subgroup(s) below {rare_group_threshold} records; "
                    "subgroup estimates for these will be imprecise"
                ),
            }
        )

    # ---- weighting / effective sample size ------------------------------ #
    weights = [float(row.get("weight", 1.0) or 0.0) for row in rows]
    weights_vary = bool(weights) and len({round(item, 9) for item in weights}) > 1
    if weights and sum(item * item for item in weights) > 0:
        effective = (sum(weights) ** 2) / sum(item * item for item in weights)
    else:
        effective = float(total)
    effective_sample_size = _round(min(effective, float(total)) if total else 0.0, 4)

    source_counts: dict[str, int] = {}
    for row in rows:
        source = str(row.get("source") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1

    # ---- global findings ------------------------------------------------ #
    if target_size and total != target_size:
        findings.append(
            {
                "severity": "warning",
                "code": "size_mismatch",
                "message": f"declared target_size {target_size}, realized {total}",
            }
        )
    if duplicate_count:
        findings.append(
            {
                "severity": "warning",
                "code": "duplicates",
                "message": f"{duplicate_count} record(s) duplicate an earlier dimension vector",
            }
        )
    if contradiction_hits:
        findings.append(
            {
                "severity": "warning",
                "code": "contradictions",
                "message": (
                    f"{contradiction_hits} of {total} record(s) fail cross-dimension consistency "
                    f"(rate {contradiction_rate}); the upstream sources measure "
                    f"{CORPUS_CONTRADICTION_BASELINE['matraix-persona-dev-sample']} "
                    f"(dev sample) and {CORPUS_CONTRADICTION_BASELINE['full_dag_sample']} "
                    "(Full-DAG) at baseline, so a non-zero rate is expected rather than a "
                    "materialization defect"
                ),
            }
        )
    if not total:
        findings.append({"severity": "error", "code": "empty_population", "message": "no records materialized"})

    return QualityReport(
        total=total,
        realized_size=total,
        target_size=int(target_size),
        coverage=coverage,
        missingness=missingness,
        duplicate_rate=duplicate_rate,
        duplicate_count=duplicate_count,
        contradiction_rate=contradiction_rate,
        contradictions=contradictions,
        constraint_satisfaction=constraint_satisfaction,
        distribution_deviation=distribution_deviation,
        segment_counts=dict(sorted(segment_counts.items())),
        subgroup_counts=subgroup_counts,
        rare_groups=rare_groups,
        effective_sample_size=effective_sample_size,
        weights_vary=weights_vary,
        source_counts=dict(sorted(source_counts.items())),
        findings=findings,
    )
