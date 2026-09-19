"""Enterprise console navigation, wizard steps, and copy.

The Playground SPA and the API-served ``/console`` page share this contract.
Dashboards beyond artifact views stay stubs until Phase 8.
"""

from __future__ import annotations

from typing import Any

from matraix.enterprise.evaluation import (
    HUMAN_VALIDATION_NOTE,
    SYNTHETIC_EVALUATION_LIMITATION,
)

CONSOLE_NAV: tuple[tuple[str, str], ...] = (
    ("overview", "Overview"),
    ("organizations", "Organizations"),
    ("populations", "Populations"),
    ("personas", "Personas"),
    ("experiments", "Experiments"),
    ("tasks", "Tasks"),
    ("environments", "Environments"),
    ("models", "Models"),
    ("evaluations", "Evaluations"),
    ("analytics", "Analytics"),
    ("governance", "Governance"),
    ("audit", "Audit"),
    ("infrastructure", "Infrastructure"),
    ("settings", "Settings"),
)

WIZARD_STEPS: tuple[tuple[str, str], ...] = (
    ("population", "Population"),
    ("scenario", "Scenario"),
    ("task", "Task"),
    ("environment", "Environment"),
    ("ai_system", "AI System"),
    ("metrics", "Metrics"),
    ("governance", "Governance"),
    ("scale", "Scale"),
    ("cost", "Cost"),
    ("launch", "Launch"),
)

SYNTHETIC_DISCLAIMER = SYNTHETIC_EVALUATION_LIMITATION
HUMAN_VALIDATION = HUMAN_VALIDATION_NOTE


def console_manifest() -> dict[str, Any]:
    return {
        "nav": [{"id": key, "label": label} for key, label in CONSOLE_NAV],
        "wizard": [{"id": key, "label": label} for key, label in WIZARD_STEPS],
        "limitation": SYNTHETIC_DISCLAIMER,
        "recommended_human_validation": True,
        "synthetic_equivalent_to_human_research": False,
        "human_validation": HUMAN_VALIDATION,
        "default_policy": "SANDBOX_ONLY",
        "stub_pages": [
            "tasks",
            "environments",
            "governance",
            "audit",
            "infrastructure",
        ],
    }
