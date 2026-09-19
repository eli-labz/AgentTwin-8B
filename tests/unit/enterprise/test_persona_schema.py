from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from matraix.enterprise.errors import EnterpriseSchemaError
from matraix.enterprise.persona_schema import parse_legacy_persona

REPO_ROOT = Path(__file__).resolve().parents[3]
LEGACY_YAML = (
    REPO_ROOT
    / "persona"
    / "datasets"
    / "matraix-persona-dev-sample"
    / "persona_0042.yaml"
)


def test_existing_dev_sample_persona_is_valid() -> None:
    record = yaml.safe_load(LEGACY_YAML.read_text(encoding="utf-8"))
    parsed = parse_legacy_persona(record)
    assert parsed["persona_id"] == "0042"
    assert parsed["source"] == "amazon"
    assert parsed["dimensions"]["role_function"] == "Teaching"
    assert parsed["enterprise"] is None


def test_missing_persona_id_fails() -> None:
    with pytest.raises(EnterpriseSchemaError, match="persona_id"):
        parse_legacy_persona({"dimensions": {"age_bracket": "25-34"}})


def test_empty_dimensions_fail() -> None:
    with pytest.raises(EnterpriseSchemaError, match="non-empty"):
        parse_legacy_persona({"persona_id": "1", "dimensions": {}})


def test_catalog_mismatch_fails() -> None:
    with pytest.raises(EnterpriseSchemaError, match="not in the catalog"):
        parse_legacy_persona(
            {
                "persona_id": "1",
                "dimensions": {"age_bracket": "not-a-real-bracket"},
            }
        )


def test_counterfactual_core_dimensions_fail() -> None:
    with pytest.raises(EnterpriseSchemaError, match="inconsistent"):
        parse_legacy_persona(
            {
                "persona_id": "1",
                "dimensions": {
                    "age_bracket": "18-24",
                    "life_stage": "Retirement",
                    "seniority": "Retired",
                    "years_experience": "20+",
                    "highest_education": "Secondary",
                },
            }
        )


def test_unknown_enterprise_field_fails() -> None:
    with pytest.raises(EnterpriseSchemaError, match="unknown fields"):
        parse_legacy_persona(
            {
                "persona_id": "1",
                "dimensions": {"age_bracket": "25-34"},
                "enterprise": {"not_a_group": {"x": "y"}},
            }
        )


def test_non_string_enterprise_value_fails() -> None:
    with pytest.raises(EnterpriseSchemaError, match="must be a string"):
        parse_legacy_persona(
            {
                "persona_id": "1",
                "dimensions": {"age_bracket": "25-34"},
                "enterprise": {"employment": {"department": 12}},
            }
        )
