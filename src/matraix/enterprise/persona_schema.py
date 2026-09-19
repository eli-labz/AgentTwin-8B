"""Validate existing persona YAML plus optional enterprise extensions.

Existing Playground / Harbor records stay valid without enterprise fields.
Invalid catalogs, counterfactual core dimensions, and malformed enterprise
blocks fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from matraix.enterprise.errors import EnterpriseSchemaError
from matraix.persona_consistency import validate_dimensions
from matraix.persona_generator import DEFAULT_CATALOG_PATH, load_catalog_values

_OPTIONAL_STRING_GROUPS = (
    "organization",
    "employment",
    "capabilities",
    "behavior",
    "work_context",
    "accessibility",
    "ai_interaction",
)

_ORGANIZATION_FIELDS = (
    "industry",
    "company_size",
    "operating_model",
    "geography",
    "regulatory_environment",
)
_EMPLOYMENT_FIELDS = (
    "department",
    "team",
    "role",
    "seniority",
    "tenure",
    "employment_type",
    "manager_level",
    "reporting_structure",
)
_CAPABILITY_FIELDS = (
    "domain_expertise",
    "technical_skill",
    "ai_literacy",
    "digital_literacy",
    "process_knowledge",
    "product_knowledge",
)
_BEHAVIOR_FIELDS = (
    "risk_tolerance",
    "change_resistance",
    "autonomy_preference",
    "communication_style",
    "collaboration_preference",
    "escalation_tendency",
    "compliance_orientation",
)
_WORK_CONTEXT_FIELDS = (
    "workload",
    "interruption_rate",
    "time_pressure",
    "tool_complexity",
    "information_access",
    "decision_authority",
)
_ACCESSIBILITY_FIELDS = ("accommodations", "interface_requirements")
_AI_INTERACTION_FIELDS = (
    "trust_in_ai",
    "prior_ai_experience",
    "verification_behavior",
    "automation_bias",
    "willingness_to_delegate",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


@lru_cache(maxsize=4)
def load_dimension_catalog(catalog_path: str | None = None) -> dict[str, list[str]]:
    path = Path(catalog_path or DEFAULT_CATALOG_PATH)
    if not path.is_file():
        path = _repo_root() / str(catalog_path or DEFAULT_CATALOG_PATH)
    return load_catalog_values(path)


def _optional_str(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EnterpriseSchemaError(f"{field_name} must be a string")
    text = value.strip()
    if not text:
        return None
    if len(text) > 512:
        raise EnterpriseSchemaError(f"{field_name} exceeds 512 characters")
    return text


def _group(payload: Any, *, group: str, allowed: tuple[str, ...]) -> dict[str, str]:
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise EnterpriseSchemaError(f"enterprise.{group} must be an object")
    unknown = sorted(str(key) for key in payload if str(key) not in allowed)
    if unknown:
        raise EnterpriseSchemaError(
            f"enterprise.{group} has unknown fields: {', '.join(unknown)}"
        )
    out: dict[str, str] = {}
    for key in allowed:
        parsed = _optional_str(payload.get(key), field_name=f"enterprise.{group}.{key}")
        if parsed is not None:
            out[key] = parsed
    return out


@dataclass(frozen=True, slots=True)
class EnterpriseDimensions:
    """Optional enterprise simulation parameters.

    These fields do not make a synthetic persona psychologically equivalent to
    a real human. They are labeled simulation parameters only.
    """

    organization: dict[str, str] = field(default_factory=dict)
    employment: dict[str, str] = field(default_factory=dict)
    capabilities: dict[str, str] = field(default_factory=dict)
    behavior: dict[str, str] = field(default_factory=dict)
    work_context: dict[str, str] = field(default_factory=dict)
    accessibility: dict[str, str] = field(default_factory=dict)
    ai_interaction: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in _OPTIONAL_STRING_GROUPS:
            value = getattr(self, name)
            object.__setattr__(self, name, dict(value or {}))


def validate_enterprise_dimensions(dims: EnterpriseDimensions) -> None:
    """Re-check a constructed enterprise block (unknown keys already rejected)."""
    for name in _OPTIONAL_STRING_GROUPS:
        value = getattr(dims, name)
        if not isinstance(value, dict):
            raise EnterpriseSchemaError(f"enterprise.{name} must be an object")
        for key, item in value.items():
            if not isinstance(key, str) or not isinstance(item, str):
                raise EnterpriseSchemaError(
                    f"enterprise.{name}.{key} must be a string pair"
                )


def parse_enterprise_block(payload: Any) -> EnterpriseDimensions | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise EnterpriseSchemaError("enterprise must be an object")
    allowed_top = set(_OPTIONAL_STRING_GROUPS)
    unknown = sorted(str(key) for key in payload if str(key) not in allowed_top)
    if unknown:
        raise EnterpriseSchemaError(
            f"enterprise has unknown fields: {', '.join(unknown)}"
        )
    dims = EnterpriseDimensions(
        organization=_group(
            payload.get("organization"),
            group="organization",
            allowed=_ORGANIZATION_FIELDS,
        ),
        employment=_group(
            payload.get("employment"), group="employment", allowed=_EMPLOYMENT_FIELDS
        ),
        capabilities=_group(
            payload.get("capabilities"),
            group="capabilities",
            allowed=_CAPABILITY_FIELDS,
        ),
        behavior=_group(
            payload.get("behavior"), group="behavior", allowed=_BEHAVIOR_FIELDS
        ),
        work_context=_group(
            payload.get("work_context"),
            group="work_context",
            allowed=_WORK_CONTEXT_FIELDS,
        ),
        accessibility=_group(
            payload.get("accessibility"),
            group="accessibility",
            allowed=_ACCESSIBILITY_FIELDS,
        ),
        ai_interaction=_group(
            payload.get("ai_interaction"),
            group="ai_interaction",
            allowed=_AI_INTERACTION_FIELDS,
        ),
    )
    validate_enterprise_dimensions(dims)
    return dims


def validate_dimension_assignments(
    dimensions: Mapping[str, Any],
    *,
    catalog: Mapping[str, list[str]] | None = None,
) -> dict[str, str]:
    """Fail closed on non-string values, catalog mismatches, and contradictions."""
    if not isinstance(dimensions, Mapping) or not dimensions:
        raise EnterpriseSchemaError("dimensions must be a non-empty object")
    normalized: dict[str, str] = {}
    for key, value in dimensions.items():
        dim_id = str(key or "").strip()
        if not dim_id:
            raise EnterpriseSchemaError("dimension id must be a non-empty string")
        if value is None:
            raise EnterpriseSchemaError(f"dimension {dim_id!r} value is missing")
        if not isinstance(value, str):
            raise EnterpriseSchemaError(f"dimension {dim_id!r} value must be a string")
        text = value.strip()
        if not text:
            raise EnterpriseSchemaError(f"dimension {dim_id!r} value is empty")
        normalized[dim_id] = text

    values = catalog if catalog is not None else load_dimension_catalog()
    for dim_id, text in normalized.items():
        allowed = values.get(dim_id)
        if allowed is not None and text not in allowed:
            raise EnterpriseSchemaError(
                f"dimension {dim_id!r} value {text!r} is not in the catalog"
            )

    contradictions = validate_dimensions(normalized)
    if contradictions:
        raise EnterpriseSchemaError(
            "persona dimensions are internally inconsistent: "
            + "; ".join(contradictions)
        )
    return normalized


def parse_legacy_persona(
    record: Mapping[str, Any],
    *,
    catalog: Mapping[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Parse a Playground/Harbor persona YAML mapping into a validated dict."""
    if not isinstance(record, Mapping):
        raise EnterpriseSchemaError("persona record must be an object")
    persona_id = str(record.get("persona_id") or record.get("id") or "").strip()
    if not persona_id:
        raise EnterpriseSchemaError("persona_id is required")
    dimensions = validate_dimension_assignments(
        record.get("dimensions") or {}, catalog=catalog
    )
    provenance = record.get("provenance")
    if provenance is not None and not isinstance(provenance, Mapping):
        raise EnterpriseSchemaError("provenance must be an object")
    display_name = record.get("display_name")
    if display_name is not None and not isinstance(display_name, str):
        raise EnterpriseSchemaError("display_name must be a string")
    return {
        "persona_id": persona_id,
        "version": str(record.get("version") or "1.0"),
        "source": str(record.get("source") or ""),
        "display_name": display_name.strip() if isinstance(display_name, str) else None,
        "dimensions": dimensions,
        "provenance": dict(provenance) if isinstance(provenance, Mapping) else None,
        "enterprise": parse_enterprise_block(record.get("enterprise")),
    }
