"""Hierarchical metrics from step up to enterprise.

Aggregation is in-process and tenant-scoped by the caller. Confidence
intervals use a normal approximation and are omitted when ``n < 2``.
Synthetic-user metrics are never labeled as human research.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from matraix.enterprise.ids import ExecutionId, ExperimentId, TenantId

SYNTHETIC_METRIC_LIMITATION = (
    "Synthetic-user metrics are simulation parameters, not equivalent to "
    "employee or customer research."
)

LEVEL_ORDER = (
    "step",
    "task",
    "session",
    "persona",
    "cohort",
    "population",
    "experiment",
    "enterprise",
)


class MetricLevel(str, Enum):
    STEP = "step"
    TASK = "task"
    SESSION = "session"
    PERSONA = "persona"
    COHORT = "cohort"
    POPULATION = "population"
    EXPERIMENT = "experiment"
    ENTERPRISE = "enterprise"


@dataclass(frozen=True, slots=True)
class MetricPoint:
    name: str
    value: float
    level: MetricLevel
    tenant_id: TenantId
    unit: str = "count"
    n: int = 1
    segments: dict[str, str] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    execution_id: ExecutionId | None = None
    experiment_id: ExperimentId | None = None

    def __post_init__(self) -> None:
        level = self.level if isinstance(self.level, MetricLevel) else MetricLevel(self.level)
        object.__setattr__(self, "level", level)
        object.__setattr__(self, "name", str(self.name).strip())
        object.__setattr__(self, "value", float(self.value))
        object.__setattr__(self, "n", max(1, int(self.n)))
        object.__setattr__(self, "segments", dict(self.segments or {}))
        object.__setattr__(self, "provenance", dict(self.provenance or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "level": self.level.value,
            "tenant_id": str(self.tenant_id),
            "unit": self.unit,
            "n": self.n,
            "segments": dict(self.segments),
            "provenance": dict(self.provenance),
            "execution_id": self.execution_id.value if self.execution_id else None,
            "experiment_id": self.experiment_id.value if self.experiment_id else None,
        }


@dataclass(frozen=True, slots=True)
class AggregatedMetric:
    name: str
    level: MetricLevel
    n: int
    mean: float
    minimum: float
    maximum: float
    ci_low: float | None
    ci_high: float | None
    unit: str
    segments: dict[str, str]
    provenance: dict[str, Any]
    limitation: str = SYNTHETIC_METRIC_LIMITATION

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "level": self.level.value,
            "n": self.n,
            "mean": self.mean,
            "min": self.minimum,
            "max": self.maximum,
            "ci95": None if self.ci_low is None else [self.ci_low, self.ci_high],
            "unit": self.unit,
            "segments": dict(self.segments),
            "provenance": dict(self.provenance),
            "limitation": self.limitation,
        }


def _confidence_interval(values: list[float]) -> tuple[float | None, float | None]:
    if len(values) < 2:
        return None, None
    mean = statistics.fmean(values)
    stdev = statistics.stdev(values)
    if stdev == 0:
        return mean, mean
    half = 1.96 * stdev / math.sqrt(len(values))
    return mean - half, mean + half


def _segment_key(segments: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(k), str(v)) for k, v in segments.items()))


class MetricsRegistry:
    """Collect points and roll them up the hierarchy."""

    def __init__(self) -> None:
        self._points: list[MetricPoint] = []

    def record(self, point: MetricPoint) -> MetricPoint:
        self._points.append(point)
        return point

    def points(
        self,
        *,
        tenant_id: TenantId | None = None,
        level: MetricLevel | None = None,
    ) -> list[MetricPoint]:
        items = list(self._points)
        if tenant_id is not None:
            items = [item for item in items if item.tenant_id == tenant_id]
        if level is not None:
            items = [item for item in items if item.level is level]
        return items

    def aggregate(
        self,
        name: str,
        *,
        tenant_id: TenantId | None = None,
        level: MetricLevel | None = None,
        segment_by: str | None = None,
    ) -> list[AggregatedMetric]:
        items = [
            item
            for item in self.points(tenant_id=tenant_id, level=level)
            if item.name == name
        ]
        groups: dict[tuple[Any, ...], list[MetricPoint]] = {}
        for item in items:
            key_level = item.level
            if segment_by:
                groups.setdefault(
                    (key_level, item.segments.get(segment_by, "")), []
                ).append(item)
            else:
                groups.setdefault((key_level, _segment_key(item.segments)), []).append(item)
        rolled: list[AggregatedMetric] = []
        for (_, _seg), members in groups.items():
            values = [member.value for member in members]
            ci_low, ci_high = _confidence_interval(values)
            first = members[0]
            rolled.append(
                AggregatedMetric(
                    name=name,
                    level=first.level,
                    n=len(values),
                    mean=statistics.fmean(values),
                    minimum=min(values),
                    maximum=max(values),
                    ci_low=ci_low,
                    ci_high=ci_high,
                    unit=first.unit,
                    segments=dict(first.segments),
                    provenance={
                        **first.provenance,
                        "limitation": SYNTHETIC_METRIC_LIMITATION,
                    },
                )
            )
        order = {level.value: index for index, level in enumerate(MetricLevel)}
        rolled.sort(key=lambda item: order.get(item.level.value, 99))
        return rolled

    def hierarchy(self, name: str, *, tenant_id: TenantId | None = None) -> list[str]:
        present = {item.level.value for item in self.points(tenant_id=tenant_id) if item.name == name}
        return [level for level in LEVEL_ORDER if level in present]

    def to_dict(self, *, tenant_id: TenantId | None = None) -> dict[str, Any]:
        items = self.points(tenant_id=tenant_id)
        return {
            "points": [item.to_dict() for item in items],
            "levels": [level.value for level in MetricLevel],
            "limitation": SYNTHETIC_METRIC_LIMITATION,
        }
