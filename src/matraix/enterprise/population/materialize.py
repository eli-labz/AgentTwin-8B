"""Deterministic population materialization.

Turns a validated :class:`~matraix.enterprise.population_builder.PopulationDeclaration`
into an **immutable** :class:`~matraix.enterprise.domain.PopulationVersion`: persona
YAML on disk (the format Harbor's ``persona_path`` already consumes, plus a
``manifest.json`` so existing MatrAIx tooling can read the output as a pool),
one :class:`~matraix.enterprise.domain.PersonaSnapshot` row per persona, and a
full record of how the population was constructed.

Backends reuse the existing generation pipelines; none of them is rewritten:

============  =====================================================================
``full_dag``  :func:`matraix.persona_generator.generate_persona_pool` (Full-DAG)
``coreset_1m`` :func:`sample_production_1m` over the public 1M coreset
``treiver``   evidence-grounded records already extracted into an on-disk pool
============  =====================================================================

A declaration may mix backends per segment (hybrid construction) by setting a
segment constraint ``backend``.

**Determinism.** Per-segment seeds derive only from the caller's seed, the
segment index and the segment name — never from a random record id — so the
same declaration and seed always produce byte-identical persona files and the
same ``manifest_hash``.

**Honesty.** No LLM is called on these paths, so ``model_version`` stays
``None``. Representativeness is not claimed: the version carries
``representativeness_claim`` describing what the inputs actually justify.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from matraix.enterprise.domain import PersonaSnapshot, PopulationVersion, PopulationVersionStatus
from matraix.enterprise.errors import EnterpriseSchemaError
from matraix.enterprise.ids import EntityKind
from matraix.enterprise.population.quality import QualityReport, analyze_population_quality
from matraix.enterprise.population_builder import GenerationBackend, PopulationDeclaration
from matraix.enterprise.records import RecordQuery

__all__ = [
    "DEFAULT_EVIDENCE_POOL",
    "MATERIALIZER_VERSION",
    "MaterializationResult",
    "materialize_population",
    "persona_pool_dir",
]

MATERIALIZER_VERSION = "agenttwin-population-materializer/1.0"
DEFAULT_EVIDENCE_POOL = "persona/datasets/matraix-persona-dev-sample"
PERSONA_SCHEMA_VERSION = "1.0"
_EVIDENCE_SOURCES = ("wiki", "amazon", "stackoverflow", "prism", "gss", "real_human_survey")
_SUMMARY_DIMENSION_LIMIT = 24


@dataclass(frozen=True)
class MaterializationResult:
    version: PopulationVersion
    quality: QualityReport
    artifact_dir: Path
    manifest_path: Path
    persona_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "population_version": self.version.to_document(),
            "quality": self.quality.to_dict(),
            "artifact_dir": str(self.artifact_dir),
            "manifest_path": str(self.manifest_path),
            "persona_count": len(self.persona_paths),
        }


def _derive_seed(seed: int, index: int, name: str) -> int:
    material = f"{int(seed)}:{int(index)}:{name}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:6], "big")


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _content_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def persona_pool_dir(artifact_root: Path, tenant_id: str, population_id: str, version_number: int) -> Path:
    return Path(artifact_root) / tenant_id / population_id / f"v{version_number}"


def _segment_backend(segment: Mapping[str, Any], default: GenerationBackend) -> GenerationBackend:
    raw = (segment.get("constraints") or {}).get("backend")
    if not raw:
        return default
    try:
        return GenerationBackend(str(raw).strip().lower())
    except ValueError as exc:
        raise EnterpriseSchemaError(
            f"segment {segment.get('name')!r}: unknown backend {raw!r}; expected one of "
            + ", ".join(item.value for item in GenerationBackend)
        ) from exc


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #


def _sample_full_dag(
    *, count: int, seed: int, filters: Mapping[str, Sequence[str]], segment_name: str
) -> list[dict[str, Any]]:
    from matraix.persona_generator import generate_persona_pool

    extra = {str(key): [str(item) for item in values] for key, values in filters.items() if values}
    try:
        rows = generate_persona_pool(
            count=int(count),
            seed=int(seed),
            extra_filters=extra or None,
            include_smoke=False,
        )
    except ValueError as exc:
        raise EnterpriseSchemaError(
            f"segment {segment_name!r}: Full-DAG cannot satisfy the declared filters ({exc})"
        ) from exc
    return [
        {
            "source": str(row.get("source") or "synthetic"),
            "dimensions": dict(row["dimensions"]),
            "display_name": row.get("display_name"),
        }
        for row in rows[:count]
    ]


def _load_pool_records(repo_root: Path, pool: str) -> list[dict[str, Any]]:
    """Read an on-disk persona pool with *full* dimensions from each YAML."""
    from matraix.persona_job import load_manifest

    pool_dir = Path(repo_root) / pool
    if not pool_dir.is_dir():
        raise EnterpriseSchemaError(f"persona pool not found: {pool}")
    entries = load_manifest(pool_dir, repo_root=Path(repo_root))
    records: list[dict[str, Any]] = []
    for entry in entries:
        path = entry.get("path")
        dimensions = entry.get("dimensions")
        if path:
            raw = yaml.safe_load((Path(repo_root) / str(path)).read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("dimensions"), dict):
                dimensions = raw["dimensions"]
        if not isinstance(dimensions, dict) or not dimensions:
            continue
        records.append(
            {
                "persona_id": str(entry.get("persona_id") or Path(str(path)).stem),
                "source": str(entry.get("source") or "unknown"),
                "display_name": entry.get("display_name"),
                "dimensions": {str(key): str(value) for key, value in dimensions.items() if value is not None},
            }
        )
    if not records:
        raise EnterpriseSchemaError(f"persona pool {pool} contains no readable personas")
    return records


def _matches(dimensions: Mapping[str, Any], filters: Mapping[str, Sequence[str]]) -> bool:
    for dimension, allowed in filters.items():
        if str(dimensions.get(dimension, "")) not in {str(item) for item in allowed}:
            return False
    return True


def _sample_evidence_pool(
    *,
    records: Sequence[Mapping[str, Any]],
    count: int,
    seed: int,
    filters: Mapping[str, Sequence[str]],
    segment_name: str,
    evidence_only: bool,
    allow_replacement: bool,
) -> list[dict[str, Any]]:
    pool = list(records)
    if evidence_only:
        pool = [row for row in pool if str(row.get("source")) in _EVIDENCE_SOURCES]
    if filters:
        pool = [row for row in pool if _matches(row.get("dimensions") or {}, filters)]
    pool.sort(key=lambda row: str(row.get("persona_id")))
    if not pool:
        raise EnterpriseSchemaError(
            f"segment {segment_name!r}: no pool personas match the declared filters"
        )
    rng = random.Random(seed)
    if len(pool) >= count:
        chosen = rng.sample(pool, count)
    elif allow_replacement:
        chosen = [rng.choice(pool) for _ in range(count)]
    else:
        raise EnterpriseSchemaError(
            f"segment {segment_name!r}: pool has {len(pool)} matching personas but {count} were requested; "
            "widen the filters, choose a larger source pool, or enable oversampling"
        )
    return [
        {
            "source": str(row.get("source") or "unknown"),
            "dimensions": dict(row["dimensions"]),
            "display_name": row.get("display_name"),
            "origin_persona_id": row.get("persona_id"),
        }
        for row in chosen
    ]


def _sample_coreset_1m(
    *,
    repo_root: Path,
    count: int,
    seed: int,
    filters: Mapping[str, Sequence[str]],
    segment_name: str,
) -> list[dict[str, Any]]:
    try:
        from backend.service.persona_1m_pool import production_1m_available, sample_production_1m
    except ImportError as exc:  # pragma: no cover - requires the Playground backend on sys.path
        raise EnterpriseSchemaError(
            "the coreset_1m backend needs the Playground backend importable "
            "(PYTHONPATH must include application/playground)"
        ) from exc
    if not production_1m_available(Path(repo_root)):
        raise EnterpriseSchemaError(
            "the Persona 1M coreset is not installed; download it into "
            "persona/datasets/matraix-persona-1m/release or choose another backend"
        )
    payload = sample_production_1m(
        repo_root=Path(repo_root),
        sample_size=int(count),
        seed=int(seed),
        dimension_filters={str(key): [str(item) for item in values] for key, values in filters.items()} or None,
    )
    rows = payload.get("personas") or []
    if len(rows) < count:
        raise EnterpriseSchemaError(
            f"segment {segment_name!r}: coreset returned {len(rows)} personas for a request of {count}"
        )
    out: list[dict[str, Any]] = []
    for row in rows[:count]:
        dimensions = row.get("dimensions") or {}
        out.append(
            {
                "source": str(row.get("source") or "coreset_1m"),
                "dimensions": {str(key): str(value) for key, value in dimensions.items() if value is not None},
                "display_name": row.get("display_name") or row.get("name"),
                "origin_persona_id": row.get("persona_id") or row.get("personaId"),
            }
        )
    return out


def _source_dataset_entry(backend: GenerationBackend, *, repo_root: Path, evidence_pool: str) -> dict[str, Any]:
    if backend is GenerationBackend.FULL_DAG:
        graph = Path(repo_root) / "persona" / "synthesis" / "graph" / "full_dag.json"
        entry: dict[str, Any] = {"kind": "full_dag", "path": "persona/synthesis/graph/full_dag.json"}
        if graph.is_file():
            entry["size_bytes"] = graph.stat().st_size
        entry["grounding"] = "synthetic"
        return entry
    if backend is GenerationBackend.CORESET_1M:
        return {
            "kind": "coreset_1m",
            "path": "persona/datasets/matraix-persona-1m",
            "hf_repo": "MatrAIx2026/MatrAIx_Persona_1M_Public_Release",
            "grounding": "mixed: human-grounded and synthetic",
        }
    return {
        "kind": "evidence_pool",
        "path": evidence_pool,
        "grounding": "evidence-grounded extraction (Treiver-derived records)",
    }


# --------------------------------------------------------------------------- #
# Materialization
# --------------------------------------------------------------------------- #


def materialize_population(
    store: Any,
    *,
    declaration: PopulationDeclaration,
    seed: int,
    artifact_root: Path,
    repo_root: Path,
    created_by: str | None = None,
    evidence_pool: str = DEFAULT_EVIDENCE_POOL,
    segment_weights: Mapping[str, float] | None = None,
    allow_replacement: bool = False,
    key_dimensions: Sequence[str] = (),
    catalog_dimension_ids: Iterable[str] | None = None,
) -> MaterializationResult:
    """Materialize ``declaration`` into a new immutable population version.

    Two distinct failure modes, deliberately different:

    * **Planning failures** (a segment's pool cannot satisfy its quota, an unknown
      backend, the coreset not installed) raise before any version record exists,
      so a rejected request leaves no orphan version and consumes no version number.
    * **Materialization failures** (I/O while writing persona files, quality
      analysis) mark the already-created version ``FAILED`` with the reason
      attached, so a partially built version is visible rather than silently lost.
    """
    spec = declaration.to_dict()
    tenant_id = declaration.tenant_id.value
    population_id = declaration.population_id.value
    default_backend = declaration.backend

    existing = store.list_records(
        tenant_id, EntityKind.POPULATION_VERSION, PopulationVersion, RecordQuery(parent_id=population_id)
    )
    version_number = max((item.version_number for item in existing), default=0) + 1

    source_datasets: list[dict[str, Any]] = []
    backends_used: set[str] = set()
    pool_cache: list[dict[str, Any]] | None = None

    records: list[dict[str, Any]] = []
    for index, segment in enumerate(spec["segments"]):
        name = str(segment["name"])
        count = int(segment["resolved_count"])
        filters = {str(key): list(values) for key, values in (segment.get("filters") or {}).items()}
        backend = _segment_backend(segment, default_backend)
        backends_used.add(backend.value)
        segment_seed = _derive_seed(seed, index, name)
        if backend is GenerationBackend.FULL_DAG:
            sampled = _sample_full_dag(count=count, seed=segment_seed, filters=filters, segment_name=name)
        elif backend is GenerationBackend.CORESET_1M:
            sampled = _sample_coreset_1m(
                repo_root=repo_root, count=count, seed=segment_seed, filters=filters, segment_name=name
            )
        else:
            if pool_cache is None:
                pool_cache = _load_pool_records(repo_root, evidence_pool)
            sampled = _sample_evidence_pool(
                records=pool_cache,
                count=count,
                seed=segment_seed,
                filters=filters,
                segment_name=name,
                evidence_only=True,
                allow_replacement=allow_replacement,
            )
        weight = float((segment_weights or {}).get(name, 1.0))
        if weight <= 0:
            raise EnterpriseSchemaError(f"segment {name!r}: weight must be > 0")
        for row in sampled:
            records.append({**row, "segment": name, "weight": weight, "backend": backend.value})

    for backend_name in sorted(backends_used):
        source_datasets.append(
            _source_dataset_entry(GenerationBackend(backend_name), repo_root=repo_root, evidence_pool=evidence_pool)
        )

    # --- version record (MATERIALIZING) ---------------------------------- #
    weights_summary = {
        "mode": "declared_segment_weights" if segment_weights else "uniform",
        "segment_weights": {key: float(value) for key, value in sorted((segment_weights or {}).items())},
    }
    version = PopulationVersion.create(
        tenant_id=tenant_id,
        organization_id=declaration.organization_id.value,
        created_by=created_by,
        population_id=population_id,
        version_number=version_number,
        status=PopulationVersionStatus.MATERIALIZING.value,
        declaration=spec,
        source_datasets=source_datasets,
        schema_version=PERSONA_SCHEMA_VERSION,
        sampling_method="+".join(sorted(backends_used)),
        seed=int(seed),
        filters={
            str(key): [str(item) for item in values]
            for segment in spec["segments"]
            for key, values in (segment.get("filters") or {}).items()
        },
        constraints=[{"expression": item} for item in spec.get("constraints", [])],
        weights_summary=weights_summary,
        generator_version=MATERIALIZER_VERSION,
        model_version=None,
        privacy_mode=spec["privacy_mode"],
        target_size=int(spec["target_size"]),
        provenance={
            "materializer": MATERIALIZER_VERSION,
            "llm_involved": False,
            "evidence_pool": evidence_pool if "treiver" in backends_used else None,
            "oversampling_with_replacement": bool(allow_replacement),
        },
    )
    store.put_record(version)

    try:
        artifact_dir = persona_pool_dir(artifact_root, tenant_id, population_id, version_number)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        summary_dimensions = [str(item) for item in key_dimensions] or sorted(
            {str(key) for segment in spec["segments"] for key in (segment.get("filters") or {})}
        )
        manifest_personas: list[dict[str, Any]] = []
        snapshots: list[PersonaSnapshot] = []
        persona_paths: list[str] = []
        quality_rows: list[dict[str, Any]] = []

        for seq, row in enumerate(records, start=1):
            persona_ref = str(seq).zfill(6)
            payload: dict[str, Any] = {
                "persona_id": persona_ref,
                "version": PERSONA_SCHEMA_VERSION,
                "source": row["source"],
                "dimensions": row["dimensions"],
            }
            if row.get("display_name"):
                payload["display_name"] = str(row["display_name"])
            file_path = artifact_dir / f"persona_{persona_ref}.yaml"
            file_path.write_text(
                yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8"
            )
            try:
                rel_path = str(file_path.relative_to(Path(repo_root)))
            except ValueError:
                rel_path = str(file_path)
            persona_paths.append(rel_path)
            digest = _content_hash(payload)
            summary = {
                dimension: str(row["dimensions"].get(dimension, ""))
                for dimension in summary_dimensions[:_SUMMARY_DIMENSION_LIMIT]
                if row["dimensions"].get(dimension)
            }
            snapshots.append(
                PersonaSnapshot(
                    population_version_id=version.id,
                    seq=seq,
                    persona_ref=persona_ref,
                    path=rel_path,
                    content_hash=digest,
                    weight=float(row["weight"]),
                    segment=row["segment"],
                    source=row["source"],
                    dimensions_summary=summary,
                )
            )
            manifest_row = {
                "persona_id": persona_ref,
                "path": rel_path,
                "source": row["source"],
                "segment": row["segment"],
                "weight": float(row["weight"]),
                "content_hash": digest,
                "dimensions": summary,
            }
            if row.get("display_name"):
                manifest_row["display_name"] = str(row["display_name"])
            if row.get("origin_persona_id"):
                manifest_row["origin_persona_id"] = str(row["origin_persona_id"])
            manifest_personas.append(manifest_row)
            quality_rows.append(
                {
                    "persona_id": persona_ref,
                    "dimensions": row["dimensions"],
                    "segment": row["segment"],
                    "weight": float(row["weight"]),
                    "source": row["source"],
                }
            )

        manifest_hash = hashlib.sha256(
            _canonical(
                [
                    {"seq": item.seq, "persona_ref": item.persona_ref, "content_hash": item.content_hash}
                    for item in snapshots
                ]
            ).encode("utf-8")
        ).hexdigest()

        catalog_ids = list(catalog_dimension_ids) if catalog_dimension_ids is not None else _catalog_ids(repo_root)
        quality = analyze_population_quality(
            quality_rows,
            target_size=int(spec["target_size"]),
            catalog_dimension_ids=catalog_ids,
            segments=spec["segments"],
            key_dimensions=summary_dimensions,
        )

        manifest = {
            "kind": "agenttwin-population-version",
            "population_id": population_id,
            "population_version_id": version.id,
            "version_number": version_number,
            "tenant_id": tenant_id,
            "seed": int(seed),
            "schema_version": PERSONA_SCHEMA_VERSION,
            "count": len(manifest_personas),
            "sampling_method": version.sampling_method,
            "generator_version": MATERIALIZER_VERSION,
            "model_version": None,
            "privacy_mode": spec["privacy_mode"],
            "manifest_hash": manifest_hash,
            "dimension_categories": "persona/schema/dimension_categories.json",
            "source_datasets": source_datasets,
            "validity": "SIMULATION_ONLY",
            "representativeness_claim": version.representativeness_claim,
            "personas": manifest_personas,
        }
        manifest_path = artifact_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")

        with store.transaction():
            store.add_persona_snapshots(tenant_id, snapshots)
            ready = version.with_update(
                status=PopulationVersionStatus.READY.value,
                realized_size=len(snapshots),
                artifact_root=str(artifact_dir),
                manifest_hash=manifest_hash,
                quality=quality.to_dict(),
                validation_findings=quality.findings,
            )
            store.put_record(ready)
    except Exception as exc:
        failed = version.with_update(
            status=PopulationVersionStatus.FAILED.value,
            validation_findings=[{"severity": "error", "code": "materialization_failed", "message": str(exc)}],
        )
        try:
            store.put_record(failed)
        except Exception:  # noqa: BLE001 - surface the original failure
            pass
        raise

    return MaterializationResult(
        version=ready,
        quality=quality,
        artifact_dir=artifact_dir,
        manifest_path=manifest_path,
        persona_paths=tuple(persona_paths),
    )


def _catalog_ids(repo_root: Path) -> list[str]:
    path = Path(repo_root) / "persona" / "schema" / "dimensions.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    dimensions = payload.get("dimensions")
    if isinstance(dimensions, list):
        return [str(item.get("id")) for item in dimensions if isinstance(item, dict) and item.get("id")]
    if isinstance(dimensions, dict):
        return [str(key) for key in dimensions]
    return []
