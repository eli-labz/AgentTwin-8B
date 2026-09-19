"""Declare a shaped population without rewriting persona/synthesis.

The builder records counts, segments, constraints, and which existing
generation backend should later fill the declaration (Treiver, Full-DAG, or
the 1M coreset). It does not sample YAML or import those pipelines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from matraix.enterprise.errors import EnterpriseSchemaError
from matraix.enterprise.ids import OrganizationId, PopulationId, TenantId
from matraix.enterprise.persona_schema import load_dimension_catalog

__all__ = [
    "DEFAULT_PRIVACY_MODE",
    "GenerationBackend",
    "PopulationDeclaration",
    "PopulationSegment",
    "build_population_declaration",
]

DEFAULT_PRIVACY_MODE = "aggregate_stats_then_synthetic"
_FORBIDDEN_PRIVACY = frozenset(
    {
        "clone_identifiable",
        "clone_identifiable_employees",
        "clone_employees",
        "copy_real_employees",
    }
)
_SHARE_TOLERANCE = 1e-6


class GenerationBackend(str, Enum):
    """Existing generation pipelines. The builder only names them."""

    TREIVER = "treiver"
    FULL_DAG = "full_dag"
    CORESET_1M = "coreset_1m"


@dataclass(frozen=True, slots=True)
class PopulationSegment:
    name: str
    count: int | None = None
    share: float | None = None
    filters: dict[str, tuple[str, ...]] = field(default_factory=dict)
    constraints: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        if not name or len(name) > 128:
            raise EnterpriseSchemaError("segment name must be 1-128 characters")
        object.__setattr__(self, "name", name)
        if self.count is not None and self.share is not None:
            raise EnterpriseSchemaError(
                f"segment {name!r} cannot set both count and share"
            )
        if self.count is None and self.share is None:
            raise EnterpriseSchemaError(
                f"segment {name!r} must set count or share"
            )
        if self.count is not None:
            if int(self.count) < 1:
                raise EnterpriseSchemaError(f"segment {name!r} count must be >= 1")
            object.__setattr__(self, "count", int(self.count))
        if self.share is not None:
            share = float(self.share)
            if share <= 0 or share > 1:
                raise EnterpriseSchemaError(
                    f"segment {name!r} share must be in (0, 1]"
                )
            object.__setattr__(self, "share", share)
        object.__setattr__(self, "filters", _filter_map(self.filters))
        object.__setattr__(self, "constraints", _constraint_map(self.constraints))


@dataclass(frozen=True, slots=True)
class PopulationDeclaration:
    """Validated population shape persisted beside a :class:`Population`."""

    tenant_id: TenantId
    organization_id: OrganizationId
    population_id: PopulationId
    target_size: int
    backend: GenerationBackend
    segments: tuple[PopulationSegment, ...]
    resolved_counts: tuple[int, ...]
    constraints: tuple[str, ...] = ()
    include_org_structure: bool = False
    privacy_mode: str = DEFAULT_PRIVACY_MODE

    def __post_init__(self) -> None:
        if self.population_id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError("declaration population is not in this tenant")
        if self.organization_id.tenant_id != self.tenant_id:
            raise EnterpriseSchemaError(
                "declaration organization is not in this tenant"
            )
        if int(self.target_size) < 1:
            raise EnterpriseSchemaError("target_size must be >= 1")
        object.__setattr__(self, "target_size", int(self.target_size))
        backend = (
            self.backend
            if isinstance(self.backend, GenerationBackend)
            else GenerationBackend(str(self.backend))
        )
        object.__setattr__(self, "backend", backend)
        if not self.segments:
            raise EnterpriseSchemaError("declaration requires at least one segment")
        object.__setattr__(self, "segments", tuple(self.segments))
        counts = tuple(int(item) for item in self.resolved_counts)
        if len(counts) != len(self.segments):
            raise EnterpriseSchemaError("resolved_counts must match segments")
        if sum(counts) != self.target_size:
            raise EnterpriseSchemaError(
                "resolved segment counts must sum to target_size"
            )
        object.__setattr__(self, "resolved_counts", counts)
        object.__setattr__(
            self, "privacy_mode", _privacy_mode(self.privacy_mode)
        )
        object.__setattr__(self, "constraints", _constraint_tuple(self.constraints))

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id.value,
            "organization_id": self.organization_id.value,
            "population_id": self.population_id.value,
            "target_size": self.target_size,
            "backend": self.backend.value,
            "include_org_structure": self.include_org_structure,
            "privacy_mode": self.privacy_mode,
            "constraints": list(self.constraints),
            "resolved_counts": list(self.resolved_counts),
            "segments": [
                {
                    "name": segment.name,
                    "count": segment.count,
                    "share": segment.share,
                    "filters": {key: list(vals) for key, vals in segment.filters.items()},
                    "constraints": dict(segment.constraints),
                    "resolved_count": self.resolved_counts[index],
                }
                for index, segment in enumerate(self.segments)
            ],
        }


def _privacy_mode(value: str | None) -> str:
    text = str(value or DEFAULT_PRIVACY_MODE).strip().lower()
    if not text:
        text = DEFAULT_PRIVACY_MODE
    if text in _FORBIDDEN_PRIVACY:
        raise EnterpriseSchemaError(
            "privacy mode forbids cloning identifiable employees; "
            f"use {DEFAULT_PRIVACY_MODE!r}"
        )
    if len(text) > 128:
        raise EnterpriseSchemaError("privacy_mode exceeds 128 characters")
    return text


def _constraint_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = [value]
    else:
        items = list(value)
    out: list[str] = []
    for raw in items:
        text = str(raw).strip()
        if not text:
            continue
        if text.lower() in _FORBIDDEN_PRIVACY:
            raise EnterpriseSchemaError(
                "constraints must not clone identifiable employees"
            )
        if len(text) > 256:
            raise EnterpriseSchemaError("constraint exceeds 256 characters")
        out.append(text)
    return tuple(out)


def _constraint_map(value: Mapping[str, Any] | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise EnterpriseSchemaError("segment constraints must be an object")
    out: dict[str, str] = {}
    for key, item in value.items():
        name = str(key or "").strip()
        if not name:
            raise EnterpriseSchemaError("constraint name is required")
        text = str(item).strip()
        if not text:
            raise EnterpriseSchemaError(f"constraint {name!r} is empty")
        if name.lower() in _FORBIDDEN_PRIVACY or text.lower() in _FORBIDDEN_PRIVACY:
            raise EnterpriseSchemaError(
                "constraints must not clone identifiable employees"
            )
        out[name] = text
    return out


def _filter_map(value: Mapping[str, Any] | None) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise EnterpriseSchemaError("segment filters must be an object")
    catalog = load_dimension_catalog()
    out: dict[str, tuple[str, ...]] = {}
    for key, raw in value.items():
        dim = str(key or "").strip()
        if not dim:
            raise EnterpriseSchemaError("filter dimension id is required")
        if isinstance(raw, str):
            values = (raw.strip(),)
        else:
            values = tuple(str(item).strip() for item in raw)
        values = tuple(item for item in values if item)
        if not values:
            raise EnterpriseSchemaError(f"filter {dim!r} needs at least one value")
        allowed = catalog.get(dim)
        if allowed is not None:
            unknown = [item for item in values if item not in allowed]
            if unknown:
                raise EnterpriseSchemaError(
                    f"filter {dim!r} values not in catalog: {', '.join(unknown)}"
                )
        out[dim] = values
    return out


def _resolve_counts(
    target_size: int, segments: tuple[PopulationSegment, ...]
) -> tuple[int, ...]:
    using_count = all(segment.count is not None for segment in segments)
    using_share = all(segment.share is not None for segment in segments)
    if using_count == using_share:
        raise EnterpriseSchemaError(
            "segments must all use count or all use share, not a mix"
        )
    if using_count:
        counts = tuple(int(segment.count or 0) for segment in segments)
        if sum(counts) != target_size:
            raise EnterpriseSchemaError(
                f"segment counts sum to {sum(counts)}, expected {target_size}"
            )
        return counts
    share_total = sum(float(segment.share or 0) for segment in segments)
    if abs(share_total - 1.0) > _SHARE_TOLERANCE:
        raise EnterpriseSchemaError(
            f"segment shares must sum to 1.0, got {share_total}"
        )
    raw = [float(segment.share or 0) * target_size for segment in segments]
    counts = [int(round(item)) for item in raw]
    counts[-1] += target_size - sum(counts)
    for segment, count in zip(segments, counts, strict=True):
        if count < 1:
            raise EnterpriseSchemaError(
                f"segment {segment.name!r} share rounded to {count} for "
                f"target_size={target_size}"
            )
    return tuple(counts)


def build_population_declaration(
    *,
    tenant_id: TenantId,
    organization_id: OrganizationId,
    population_id: PopulationId,
    target_size: int,
    backend: GenerationBackend | str,
    segments: list[PopulationSegment] | tuple[PopulationSegment, ...],
    constraints: list[str] | tuple[str, ...] = (),
    include_org_structure: bool = False,
    privacy_mode: str = DEFAULT_PRIVACY_MODE,
) -> PopulationDeclaration:
    """Validate a shaped declaration (including 10k-scale counts)."""
    parsed_segments = tuple(
        item
        if isinstance(item, PopulationSegment)
        else PopulationSegment(**item)  # type: ignore[arg-type]
        for item in segments
    )
    try:
        parsed_backend = (
            backend
            if isinstance(backend, GenerationBackend)
            else GenerationBackend(str(backend).strip().lower())
        )
    except ValueError as exc:
        raise EnterpriseSchemaError(
            "backend must be one of "
            + ", ".join(item.value for item in GenerationBackend)
        ) from exc
    resolved = _resolve_counts(int(target_size), parsed_segments)
    return PopulationDeclaration(
        tenant_id=tenant_id,
        organization_id=organization_id,
        population_id=population_id,
        target_size=int(target_size),
        backend=parsed_backend,
        segments=parsed_segments,
        resolved_counts=resolved,
        constraints=tuple(constraints),
        include_org_structure=bool(include_org_structure),
        privacy_mode=privacy_mode,
    )
