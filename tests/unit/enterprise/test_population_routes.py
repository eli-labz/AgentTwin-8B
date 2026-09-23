"""HTTP surface of the population engine.

Covers materialization (including idempotent replay), version reads, quality and
composition views, persona listing, freezing, cohort creation, permission
boundaries and tenant isolation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from matraix.enterprise.api import create_enterprise_app
from matraix.enterprise.context import EnterpriseSettings, build_context
from matraix.enterprise.ids import TenantId
from matraix.enterprise.repositories import InMemoryEnterpriseStore
from matraix.enterprise.secrets import StaticSecretProvider
from matraix.enterprise.store import create_tenant_with_default_org

REPO_ROOT = Path(__file__).resolve().parents[3]
ALPHA = "tnt_alpha"
BRAVO = "tnt_bravo"

TOKENS = {
    "alpha-researcher": {"subject": "alpha-researcher", "tenant_id": ALPHA, "roles": ["researcher"]},
    "alpha-viewer": {"subject": "alpha-viewer", "tenant_id": ALPHA, "roles": ["viewer"]},
    "alpha-operator": {"subject": "alpha-operator", "tenant_id": ALPHA, "roles": ["operator"]},
    "bravo-researcher": {"subject": "bravo-researcher", "tenant_id": BRAVO, "roles": ["researcher"]},
}


def _headers(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    store = InMemoryEnterpriseStore()
    create_tenant_with_default_org(store, name="Alpha", slug="alpha", tenant_id=TenantId(ALPHA))
    create_tenant_with_default_org(store, name="Bravo", slug="bravo", tenant_id=TenantId(BRAVO))
    context = build_context(
        store,
        secrets=StaticSecretProvider({"MATRIX_ENTERPRISE_AUTH_TOKENS": json.dumps(TOKENS)}),
        settings=EnterpriseSettings(repo_root=REPO_ROOT, artifact_root=tmp_path / "artifacts"),
    )
    return TestClient(create_enterprise_app(context=context))


def _declare(client: TestClient, token: str = "alpha-researcher") -> str:
    response = client.post(
        "/api/v1/population-declarations",
        json={
            "name": "Workforce",
            "target_size": 6,
            "backend": "treiver",
            "segments": [
                {"name": "support", "count": 4},
                {"name": "engineering", "count": 2},
            ],
        },
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    return response.json()["population_id"]


def _materialize(client: TestClient, population_id: str, **kwargs) -> dict:
    token = kwargs.pop("token", "alpha-researcher")
    headers = _headers(token, **kwargs.pop("extra_headers", {}))
    body = {"seed": 42, "key_dimensions": ["age_bracket", "region"]}
    body.update(kwargs)
    response = client.post(f"/api/v1/populations/{population_id}/versions", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_materialize_returns_a_ready_version_with_provenance(client: TestClient) -> None:
    population_id = _declare(client)
    version = _materialize(client, population_id)

    assert version["status"] == "READY"
    assert version["realized_size"] == 6
    assert version["target_size"] == 6
    assert version["version_number"] == 1
    assert version["seed"] == 42
    assert version["sampling_method"] == "treiver"
    assert version["model_version"] is None
    assert version["manifest_hash"]
    assert version["privacy_mode"] == "aggregate_stats_then_synthetic"
    assert "not validated" in version["representativeness_claim"]
    assert version["source_datasets"]


def test_materialization_is_idempotent_per_key(client: TestClient) -> None:
    population_id = _declare(client)
    first = _materialize(client, population_id, extra_headers={"Idempotency-Key": "launch-1"})
    replay = _materialize(client, population_id, extra_headers={"Idempotency-Key": "launch-1"})
    assert replay["id"] == first["id"]

    fresh = _materialize(client, population_id, extra_headers={"Idempotency-Key": "launch-2"})
    assert fresh["id"] != first["id"]
    assert fresh["version_number"] == 2

    listed = client.get(f"/api/v1/populations/{population_id}/versions", headers=_headers("alpha-viewer")).json()
    assert [item["version_number"] for item in listed] == [1, 2]


def test_quality_and_composition_views(client: TestClient) -> None:
    population_id = _declare(client)
    version = _materialize(client, population_id)
    version_id = version["id"]

    quality = client.get(f"/api/v1/population-versions/{version_id}/quality", headers=_headers("alpha-viewer"))
    assert quality.status_code == 200
    payload = quality.json()
    assert payload["validity"] == "SIMULATION_ONLY"
    assert payload["quality"]["total"] == 6
    assert payload["quality"]["constraint_satisfaction"]["overall"] == 1.0
    assert "contradiction_baseline" in payload["quality"]
    assert payload["quality"]["duplicate_rate"] == 0.0

    composition = client.get(
        f"/api/v1/population-versions/{version_id}/composition", headers=_headers("alpha-viewer")
    ).json()
    assert composition["realized_size"] == 6
    assert composition["segment_counts"] == {"engineering": 2, "support": 4}
    assert composition["validity"] == "SIMULATION_ONLY"
    # Aggregate only: a composition view never carries full persona records.
    assert "personas" not in composition
    assert "dimensions" not in json.dumps(composition)


def test_persona_listing_is_paginated_and_bounded(client: TestClient) -> None:
    population_id = _declare(client)
    version_id = _materialize(client, population_id)["id"]

    page = client.get(
        f"/api/v1/population-versions/{version_id}/personas?limit=4&offset=0", headers=_headers("alpha-viewer")
    ).json()
    assert page["total"] == 6
    assert len(page["items"]) == 4
    assert page["next_offset"] == 4
    first = page["items"][0]
    assert first["persona_ref"] == "000001"
    assert first["content_hash"] and first["path"].endswith("persona_000001.yaml")
    # Only the summarized dimensions travel, not all 1,290.
    assert set(first["dimensions_summary"]) <= {"age_bracket", "region"}

    tail = client.get(
        f"/api/v1/population-versions/{version_id}/personas?limit=4&offset=4", headers=_headers("alpha-viewer")
    ).json()
    assert len(tail["items"]) == 2 and tail["next_offset"] is None


def test_freeze_makes_the_version_terminal(client: TestClient) -> None:
    population_id = _declare(client)
    version_id = _materialize(client, population_id)["id"]

    frozen = client.post(f"/api/v1/population-versions/{version_id}/freeze", headers=_headers("alpha-researcher"))
    assert frozen.status_code == 200
    assert frozen.json()["status"] == "FROZEN"
    # Idempotent: freezing again returns the frozen version rather than erroring.
    again = client.post(f"/api/v1/population-versions/{version_id}/freeze", headers=_headers("alpha-researcher"))
    assert again.status_code == 200 and again.json()["status"] == "FROZEN"


def test_cohort_creation_is_reproducible_over_http(client: TestClient) -> None:
    population_id = _declare(client)
    version_id = _materialize(client, population_id)["id"]

    def _cohort(name: str, seed: int, **selection) -> dict:
        response = client.post(
            f"/api/v1/population-versions/{version_id}/cohorts",
            json={"name": name, "seed": seed, "size": 3, **selection},
            headers=_headers("alpha-researcher"),
        )
        assert response.status_code == 200, response.text
        return response.json()

    first = _cohort("wave-1", 7)
    repeat = _cohort("wave-1-again", 7)
    different = _cohort("wave-2", 8)

    assert first["persona_refs"] == repeat["persona_refs"]
    assert first["content_hash"] == repeat["content_hash"]
    assert different["persona_refs"] != first["persona_refs"]
    assert first["size"] == 3

    listed = client.get(
        f"/api/v1/cohorts?population_version_id={version_id}", headers=_headers("alpha-viewer")
    ).json()
    assert len(listed) == 3
    fetched = client.get(f"/api/v1/cohorts/{first['id']}", headers=_headers("alpha-viewer")).json()
    assert fetched["id"] == first["id"]


def test_permissions_separate_reading_from_materializing(client: TestClient) -> None:
    population_id = _declare(client)
    denied = client.post(
        f"/api/v1/populations/{population_id}/versions", json={"seed": 1}, headers=_headers("alpha-viewer")
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"

    version_id = _materialize(client, population_id)["id"]
    # A viewer may read quality and composition.
    assert client.get(f"/api/v1/population-versions/{version_id}/quality", headers=_headers("alpha-viewer")).status_code == 200
    # but may not freeze or create cohorts.
    assert client.post(f"/api/v1/population-versions/{version_id}/freeze", headers=_headers("alpha-viewer")).status_code == 403
    assert (
        client.post(
            f"/api/v1/population-versions/{version_id}/cohorts",
            json={"name": "x", "seed": 1, "size": 2},
            headers=_headers("alpha-viewer"),
        ).status_code
        == 403
    )


def test_population_versions_are_tenant_isolated(client: TestClient) -> None:
    population_id = _declare(client)
    version_id = _materialize(client, population_id)["id"]
    cohort = client.post(
        f"/api/v1/population-versions/{version_id}/cohorts",
        json={"name": "wave", "seed": 7, "size": 2},
        headers=_headers("alpha-researcher"),
    ).json()

    bravo = _headers("bravo-researcher")
    assert client.get(f"/api/v1/population-versions/{version_id}", headers=bravo).status_code == 404
    assert client.get(f"/api/v1/population-versions/{version_id}/quality", headers=bravo).status_code == 404
    assert client.get(f"/api/v1/population-versions/{version_id}/composition", headers=bravo).status_code == 404
    assert client.get(f"/api/v1/population-versions/{version_id}/personas", headers=bravo).status_code == 404
    assert client.post(f"/api/v1/population-versions/{version_id}/freeze", headers=bravo).status_code == 404
    assert client.get(f"/api/v1/cohorts/{cohort['id']}", headers=bravo).status_code == 404
    assert client.get("/api/v1/cohorts", headers=bravo).json() == []
    assert client.post(f"/api/v1/populations/{population_id}/versions", json={"seed": 1}, headers=bravo).status_code == 404
    # Bravo's own idempotency namespace is separate, so Alpha's key is invisible.
    assert client.get(f"/api/v1/populations/{population_id}/versions", headers=bravo).status_code == 404


def test_materialization_audits_and_reports_missing_declaration(client: TestClient) -> None:
    population = client.post(
        "/api/v1/populations", json={"name": "No declaration", "target_size": 5}, headers=_headers("alpha-researcher")
    ).json()
    missing = client.post(
        f"/api/v1/populations/{population['id']}/versions", json={"seed": 1}, headers=_headers("alpha-researcher")
    )
    assert missing.status_code == 404

    population_id = _declare(client)
    version = _materialize(client, population_id)
    audit = client.get("/api/v1/audit-events", headers=_headers("alpha-operator")).json()["items"]
    entry = next(item for item in audit if item["action"] == "population_version.materialize")
    assert entry["resource_id"] == version["id"]
    assert entry["details"]["manifest_hash"] == version["manifest_hash"]
    assert entry["details"]["realized_size"] == 6
