"""Population engine: deterministic materialization, quality, cohorts."""

from matraix.enterprise.population.cohorts import (
    CohortSelection,
    build_cohort,
    resolve_cohort_personas,
)
from matraix.enterprise.population.materialize import (
    DEFAULT_EVIDENCE_POOL,
    MATERIALIZER_VERSION,
    MaterializationResult,
    materialize_population,
    persona_pool_dir,
)
from matraix.enterprise.population.quality import (
    CORPUS_CONTRADICTION_BASELINE,
    RARE_GROUP_THRESHOLD,
    QualityReport,
    analyze_population_quality,
)

__all__ = [
    "CORPUS_CONTRADICTION_BASELINE",
    "DEFAULT_EVIDENCE_POOL",
    "MATERIALIZER_VERSION",
    "MaterializationResult",
    "CohortSelection",
    "QualityReport",
    "RARE_GROUP_THRESHOLD",
    "analyze_population_quality",
    "build_cohort",
    "materialize_population",
    "persona_pool_dir",
    "resolve_cohort_personas",
]
