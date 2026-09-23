"""Population quality metrics.

Records here use only dimensions the cross-dimension validator ignores
(``region``, ``domain``, ...), so each test isolates the metric under test.
The contradiction test builds a deliberately inconsistent record and asserts
that precondition through the validator itself rather than hard-coding a rule.
"""

from __future__ import annotations

from matraix.enterprise.population.quality import (
    CORPUS_CONTRADICTION_BASELINE,
    analyze_population_quality,
)
from matraix.persona_consistency import validate_dimensions


def _record(index: int, **extra: object) -> dict[str, object]:
    record: dict[str, object] = {
        "persona_id": f"{index:06d}",
        "dimensions": {"region": "Western Europe", "domain": "Software"},
        "source": "wiki",
        "weight": 1.0,
    }
    record.update(extra)
    return record


def _severities(report) -> dict[str, str]:
    return {item["code"]: item["severity"] for item in report.findings}


def test_empty_population_is_an_error() -> None:
    report = analyze_population_quality([], target_size=5)
    assert report.total == 0
    assert _severities(report)["empty_population"] == "error"
    assert report.ok is False


def test_coverage_and_missingness_are_measured_against_the_catalog() -> None:
    records = [
        {"persona_id": "1", "dimensions": {"region": "A", "domain": "B"}},
        {"persona_id": "2", "dimensions": {"region": "A"}},
    ]
    report = analyze_population_quality(
        records, target_size=2, catalog_dimension_ids=["region", "domain", "urbanicity", "register"]
    )
    assert report.coverage["catalog_dimension_count"] == 4.0
    assert report.coverage["observed_dimension_count"] == 2.0
    assert report.coverage["dimension_coverage"] == 0.5
    assert report.coverage["mean_dimensions_per_persona"] == 1.5
    assert report.coverage["mean_fill_ratio"] == 0.375
    # Never populated -> fully missing; half populated -> 0.5.
    assert report.missingness["urbanicity"] == 1.0
    assert report.missingness["register"] == 1.0
    assert report.missingness["domain"] == 0.5
    assert "region" not in report.missingness


def test_blank_values_count_as_missing_not_present() -> None:
    records = [{"persona_id": "1", "dimensions": {"region": "A", "domain": "   ", "register": None}}]
    report = analyze_population_quality(records, target_size=1, catalog_dimension_ids=["region", "domain", "register"])
    assert report.coverage["observed_dimension_count"] == 1.0
    assert report.missingness["domain"] == 1.0
    assert report.missingness["register"] == 1.0


def test_duplicate_detection_counts_repeat_vectors() -> None:
    records = [_record(1), _record(2), _record(3)]
    records[1]["dimensions"] = dict(records[0]["dimensions"])
    records[2]["dimensions"] = dict(records[0]["dimensions"])
    report = analyze_population_quality(records, target_size=3)
    assert report.duplicate_count == 2
    assert report.duplicate_rate == round(2 / 3, 6)
    assert _severities(report)["duplicates"] == "warning"
    assert report.ok is True


def test_distinct_vectors_have_no_duplicates() -> None:
    records = [_record(index, dimensions={"region": f"R{index}"}) for index in range(4)]
    report = analyze_population_quality(records, target_size=4)
    assert report.duplicate_count == 0 and report.duplicate_rate == 0.0
    assert "duplicates" not in _severities(report)


def test_contradictions_are_a_warning_with_the_corpus_baseline() -> None:
    inconsistent = {"age_bracket": "18-24", "life_stage": "Retirement", "seniority": "Executive"}
    # Precondition: the shared validator really does reject this combination.
    assert validate_dimensions(inconsistent), "expected the consistency validator to flag this record"

    records = [_record(1), _record(2), {"persona_id": "3", "dimensions": inconsistent}]
    report = analyze_population_quality(records, target_size=3)

    assert report.contradiction_rate == round(1 / 3, 6)
    assert report.contradictions and report.contradictions[0]["persona_ref"] == "3"
    # A non-zero rate reflects the upstream corpus, not a materialization defect,
    # so it must not fail the report.
    assert _severities(report)["contradictions"] == "warning"
    assert report.ok is True
    assert report.contradiction_baseline["validator"] == CORPUS_CONTRADICTION_BASELINE["validator"]
    message = next(item["message"] for item in report.findings if item["code"] == "contradictions")
    assert str(CORPUS_CONTRADICTION_BASELINE["matraix-persona-dev-sample"]) in message
    # The baseline must carry its sample size so a reader can judge its precision.
    assert CORPUS_CONTRADICTION_BASELINE["matraix-persona-dev-sample_n"] == 200
    assert CORPUS_CONTRADICTION_BASELINE["full_dag_sample_n"] == 10_000


def test_constraint_violations_are_errors() -> None:
    segments = [
        {"name": "eu", "resolved_count": 2, "filters": {"region": ["Western Europe"]}},
    ]
    records = [
        _record(1, segment="eu"),
        _record(2, segment="eu", dimensions={"region": "East Asia", "domain": "Software"}),
    ]
    report = analyze_population_quality(records, target_size=2, segments=segments)
    detail = report.constraint_satisfaction["segments"]["eu"]
    assert detail["realized_count"] == 2
    assert detail["satisfied_ratio"] == 0.5
    assert detail["filters"]["region"] == 0.5
    assert report.constraint_satisfaction["overall"] == 0.5
    assert _severities(report)["constraint_violation"] == "error"
    assert report.ok is False


def test_satisfied_constraints_report_full_compliance() -> None:
    segments = [{"name": "eu", "resolved_count": 2, "filters": {"region": ["Western Europe"]}}]
    records = [_record(1, segment="eu"), _record(2, segment="eu")]
    report = analyze_population_quality(records, target_size=2, segments=segments)
    assert report.constraint_satisfaction["overall"] == 1.0
    assert "constraint_violation" not in _severities(report)
    assert report.ok is True


def test_distribution_deviation_compares_observed_to_declared_shares() -> None:
    segments = [
        {"name": "support", "resolved_count": 6, "filters": {}},
        {"name": "engineering", "resolved_count": 4, "filters": {}},
    ]
    # Realized 5/5 against a declared 6/4 split.
    records = [_record(index, segment="support") for index in range(5)]
    records += [_record(index + 5, segment="engineering") for index in range(5)]
    report = analyze_population_quality(records, target_size=10, segments=segments)

    deviation = report.distribution_deviation
    assert deviation["segment_shares_declared"] == {"engineering": 0.4, "support": 0.6}
    assert deviation["segment_shares_observed"] == {"engineering": 0.5, "support": 0.5}
    assert deviation["segment_share_delta"] == {"engineering": 0.1, "support": -0.1}
    assert deviation["segment_total_variation_distance"] == 0.1
    assert report.segment_counts == {"engineering": 5, "support": 5}
    assert _severities(report)["segment_count_mismatch"] == "warning"


def test_size_mismatch_is_reported() -> None:
    report = analyze_population_quality([_record(1)], target_size=10)
    assert _severities(report)["size_mismatch"] == "warning"
    assert report.realized_size == 1 and report.target_size == 10


def test_effective_sample_size_reflects_weight_variation() -> None:
    uniform = analyze_population_quality([_record(index) for index in range(4)], target_size=4)
    assert uniform.weights_vary is False
    assert uniform.effective_sample_size == 4.0

    weighted = [
        _record(0, weight=1.0),
        _record(1, weight=1.0),
        _record(2, weight=3.0),
        _record(3, weight=3.0),
    ]
    report = analyze_population_quality(weighted, target_size=4)
    assert report.weights_vary is True
    # Kish ESS = (sum w)^2 / sum w^2 = 64 / 20 = 3.2
    assert report.effective_sample_size == 3.2
    assert report.effective_sample_size < report.total


def test_subgroup_counts_and_rare_groups_use_the_threshold() -> None:
    records = [_record(index, dimensions={"region": "Western Europe"}) for index in range(40)]
    records += [_record(index + 40, dimensions={"region": "East Asia"}) for index in range(5)]
    report = analyze_population_quality(
        records, target_size=45, key_dimensions=["region"], rare_group_threshold=10
    )
    assert report.subgroup_counts["region"] == {"East Asia": 5, "Western Europe": 40}
    assert report.rare_groups == [{"dimension": "region", "value": "East Asia", "count": 5}]
    assert _severities(report)["rare_subgroups"] == "info"
    # Rare subgroups are a coverage fact, not a failure.
    assert report.ok is True


def test_key_dimensions_default_to_declared_filter_dimensions() -> None:
    segments = [{"name": "eu", "resolved_count": 1, "filters": {"region": ["Western Europe"]}}]
    report = analyze_population_quality([_record(1, segment="eu")], target_size=1, segments=segments)
    assert "region" in report.subgroup_counts


def test_source_counts_and_digest_are_deterministic() -> None:
    records = [_record(1, source="wiki"), _record(2, source="gss"), _record(3, source="wiki")]
    first = analyze_population_quality(records, target_size=3)
    second = analyze_population_quality(records, target_size=3)
    assert first.source_counts == {"gss": 1, "wiki": 2}
    assert first.digest() == second.digest()
    assert "findings" in first.to_dict() and first.to_dict()["ok"] is first.ok
