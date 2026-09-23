"""Reproducible cohort selection over an immutable population version.

A :class:`~matraix.enterprise.domain.Cohort` is a named, content-hashed subset of
one :class:`~matraix.enterprise.domain.PopulationVersion`. Because the version is
immutable and selection is seeded deterministically, the same
``(population_version, seed, selection)`` always yields the same personas in the
same order — that is what makes an experiment repeatable.

Selection supports:

``size``
    how many personas to draw (``None`` takes every match).
``filters``
    dimension → allowed values. Resolved against each snapshot's stored
    dimension summary; when a requested dimension was not summarized at
    materialization time the persona YAML on disk is consulted instead.
``stratify_by``
    dimension(s) whose observed values get balanced allocation, remainder
    distributed by the largest-remainder method so counts sum exactly to ``size``.
``segment_shares``
    explicit share of the cohort per declaration segment.

Weights travel with each persona so downstream metrics can report weighted and
unweighted values side by side.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from matraix.enterprise.domain import Cohort, PersonaSnapshot, PopulationVersion, PopulationVersionStatus
from matraix.enterprise.errors import EnterpriseSchemaError

__all__ = ["CohortSelection", "build_cohort", "resolve_cohort_personas"]

_SHARE_TOLERANCE = 1e-6


@dataclass(frozen=True)
class CohortSelection:
    """Declarative, hashable cohort spec."""

    size: int | None = None
    filters: Mapping[str, Sequence[str]] = None  # type: ignore[assignment]
    stratify_by: Sequence[str] = ()
    segment_shares: Mapping[str, float] = None  # type: ignore[assignment]

    def normalized(self) -> dict[str, Any]:
        filters = {
            str(key): sorted({str(item) for item in values})
            for key, values in (self.filters or {}).items()
            if values
        }
        shares = {str(key): float(value) for key, value in (self.segment_shares or {}).items()}
        if shares:
            total = sum(shares.values())
            if abs(total - 1.0) > _SHARE_TOLERANCE:
                raise EnterpriseSchemaError(f"segment_shares must sum to 1.0, got {total}")
            for name, value in shares.items():
                if value <= 0 or value > 1:
                    raise EnterpriseSchemaError(f"segment share for {name!r} must be in (0, 1]")
        if self.size is not None and int(self.size) < 1:
            raise EnterpriseSchemaError("cohort size must be >= 1")
        return {
            "size": int(self.size) if self.size is not None else None,
            "filters": dict(sorted(filters.items())),
            "stratify_by": [str(item) for item in self.stratify_by],
            "segment_shares": dict(sorted(shares.items())),
        }


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _derive_seed(manifest_hash: str, seed: int, selection: Mapping[str, Any]) -> int:
    material = f"{manifest_hash}:{int(seed)}:{_canonical(selection)}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def _dimension_value(
    snapshot: PersonaSnapshot,
    dimension: str,
    *,
    repo_root: Path | None,
    cache: dict[str, dict[str, str]],
) -> str:
    """Read a dimension from the snapshot summary, falling back to persona YAML."""
    if dimension in snapshot.dimensions_summary:
        return str(snapshot.dimensions_summary[dimension])
    if snapshot.persona_ref in cache:
        return str(cache[snapshot.persona_ref].get(dimension, ""))
    if repo_root is None:
        raise EnterpriseSchemaError(
            f"dimension {dimension!r} is not in the population version's stored summary; "
            "pass repo_root so the persona records can be read, or include it in "
            "key_dimensions when materializing"
        )
    path = Path(repo_root) / snapshot.path
    if not path.is_file():
        path = Path(snapshot.path)
    if not path.is_file():
        raise EnterpriseSchemaError(f"persona record missing on disk: {snapshot.path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    dimensions = raw.get("dimensions") if isinstance(raw, dict) else None
    resolved = {str(key): str(value) for key, value in (dimensions or {}).items() if value is not None}
    cache[snapshot.persona_ref] = resolved
    return resolved.get(dimension, "")


def _largest_remainder(total: int, weights: Mapping[str, float]) -> dict[str, int]:
    positive = {key: float(value) for key, value in weights.items() if float(value) > 0}
    if not positive or total <= 0:
        return {key: 0 for key in weights}
    weight_total = sum(positive.values())
    raw = {key: (value / weight_total) * total for key, value in positive.items()}
    floors = {key: int(value) for key, value in raw.items()}
    remainder = total - sum(floors.values())
    ranked = sorted(positive, key=lambda key: (-(raw[key] - floors[key]), key))
    for index in range(remainder):
        floors[ranked[index % len(ranked)]] += 1
    return {key: floors.get(key, 0) for key in weights}


def resolve_cohort_personas(
    snapshots: Sequence[PersonaSnapshot],
    *,
    manifest_hash: str,
    seed: int,
    selection: CohortSelection | Mapping[str, Any] | None = None,
    repo_root: Path | None = None,
) -> tuple[list[PersonaSnapshot], dict[str, Any]]:
    """Return the chosen snapshots (stable order) and the normalized selection."""
    spec = (
        selection.normalized()
        if isinstance(selection, CohortSelection)
        else CohortSelection(
            size=(selection or {}).get("size"),
            filters=(selection or {}).get("filters"),
            stratify_by=(selection or {}).get("stratify_by") or (),
            segment_shares=(selection or {}).get("segment_shares"),
        ).normalized()
    )

    candidates = sorted(snapshots, key=lambda item: item.seq)
    cache: dict[str, dict[str, str]] = {}
    for dimension, allowed in spec["filters"].items():
        wanted = set(allowed)
        candidates = [
            item
            for item in candidates
            if _dimension_value(item, dimension, repo_root=repo_root, cache=cache) in wanted
        ]
    if not candidates:
        raise EnterpriseSchemaError("cohort selection matched no personas in this population version")

    size = spec["size"] if spec["size"] is not None else len(candidates)
    if size > len(candidates):
        raise EnterpriseSchemaError(
            f"cohort size {size} exceeds the {len(candidates)} matching personas in this population version"
        )
    rng = random.Random(_derive_seed(manifest_hash, seed, spec))

    def _draw(pool: list[PersonaSnapshot], count: int) -> list[PersonaSnapshot]:
        if count >= len(pool):
            return list(pool)
        return rng.sample(pool, count)

    chosen: list[PersonaSnapshot]
    if spec["segment_shares"]:
        buckets: dict[str, list[PersonaSnapshot]] = {}
        for item in candidates:
            buckets.setdefault(str(item.segment or ""), []).append(item)
        missing = sorted(set(spec["segment_shares"]) - set(buckets))
        if missing:
            raise EnterpriseSchemaError(
                "segment_shares name segments absent from this population version: " + ", ".join(missing)
            )
        quotas = _largest_remainder(size, spec["segment_shares"])
        chosen = []
        for name in sorted(quotas):
            quota = quotas[name]
            pool = buckets.get(name, [])
            if quota > len(pool):
                raise EnterpriseSchemaError(
                    f"segment {name!r} needs {quota} personas but only {len(pool)} match the filters"
                )
            chosen.extend(_draw(pool, quota))
    elif spec["stratify_by"]:
        buckets: dict[str, list[PersonaSnapshot]] = {}
        for item in candidates:
            key = _canonical(
                [_dimension_value(item, dimension, repo_root=repo_root, cache=cache) for dimension in spec["stratify_by"]]
            )
            buckets.setdefault(key, []).append(item)
        quotas = _largest_remainder(size, {key: float(len(value)) for key, value in buckets.items()})
        chosen = []
        shortfall = 0
        for key in sorted(buckets):
            quota = min(quotas.get(key, 0), len(buckets[key]))
            shortfall += quotas.get(key, 0) - quota
            chosen.extend(_draw(buckets[key], quota))
        if shortfall:
            taken = {item.persona_ref for item in chosen}
            remaining = [item for item in candidates if item.persona_ref not in taken]
            chosen.extend(_draw(remaining, min(shortfall, len(remaining))))
    else:
        chosen = _draw(list(candidates), size)

    chosen.sort(key=lambda item: item.seq)
    return chosen, spec


def build_cohort(
    store: Any,
    *,
    version: PopulationVersion,
    name: str,
    seed: int,
    selection: CohortSelection | Mapping[str, Any] | None = None,
    repo_root: Path | None = None,
    created_by: str | None = None,
) -> Cohort:
    """Create and persist a reproducible cohort of ``version``."""
    if version.status not in {PopulationVersionStatus.READY.value, PopulationVersionStatus.FROZEN.value}:
        raise EnterpriseSchemaError(
            f"population version {version.id} is {version.status}; cohorts need a READY or FROZEN version"
        )
    snapshots = store.list_persona_snapshots(version.tenant_id, version.id)
    if not snapshots:
        raise EnterpriseSchemaError(f"population version {version.id} has no persona snapshots")

    chosen, spec = resolve_cohort_personas(
        snapshots,
        manifest_hash=version.manifest_hash or "",
        seed=seed,
        selection=selection,
        repo_root=repo_root,
    )
    persona_refs = [item.persona_ref for item in chosen]
    weights = {item.persona_ref: float(item.weight) for item in chosen}
    content_hash = hashlib.sha256(
        _canonical(
            {
                "population_version_id": version.id,
                "manifest_hash": version.manifest_hash,
                "seed": int(seed),
                "selection": spec,
                "persona_refs": persona_refs,
            }
        ).encode("utf-8")
    ).hexdigest()

    cohort = Cohort.create(
        tenant_id=version.tenant_id,
        organization_id=version.organization_id,
        created_by=created_by,
        name=name,
        population_version_id=version.id,
        seed=int(seed),
        size=len(persona_refs),
        selection=spec,
        persona_refs=persona_refs,
        weights=weights,
        content_hash=content_hash,
        provenance={
            "population_id": version.population_id,
            "population_version_number": version.version_number,
            "manifest_hash": version.manifest_hash,
            "reproducible": True,
        },
    )
    return store.put_record(cohort)
