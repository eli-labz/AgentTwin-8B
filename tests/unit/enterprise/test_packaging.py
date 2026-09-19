"""Phase 10 packaging references stay cloud-neutral and secret-free."""

from __future__ import annotations

from pathlib import Path

import pytest

from matraix.enterprise.migrations import LATEST_SCHEMA_VERSION, SCHEMA_MIGRATIONS
from matraix.launch_env import find_repo_root

ROOT = find_repo_root(Path(__file__))
DEPLOY = ROOT / "deploy" / "enterprise"
K8S = DEPLOY / "k8s"

CLOUD_MARKERS = (
    "eks.amazonaws.com",
    "cloud.google.com/gke",
    "azure.com/aks",
    "service.beta.kubernetes.io/aws-load-balancer",
)
SECRET_MARKERS = (
    "sk-ant-",
    "sk-proj-",
    "BEGIN PRIVATE KEY",
    "AKIA",
    "ghp_",
    "xoxb-",
)


def _text_files() -> list[Path]:
    files = [DEPLOY / "Dockerfile", DEPLOY / "docker-compose.yml", DEPLOY / ".env.example"]
    files.extend(sorted(K8S.glob("*.yaml")))
    return files


def test_deploy_layout_exists() -> None:
    required = [
        DEPLOY / "Dockerfile",
        DEPLOY / "docker-compose.yml",
        DEPLOY / ".env.example",
        K8S / "kustomization.yaml",
        K8S / "namespace.yaml",
        K8S / "configmap.yaml",
        K8S / "deployment.yaml",
        K8S / "service.yaml",
        K8S / "pvc.yaml",
        ROOT / "scripts" / "enterprise_ci.sh",
        ROOT / ".github" / "workflows" / "enterprise.yml",
        ROOT / "docs" / "enterprise" / "scale.md",
        ROOT / "docs" / "enterprise" / "packaging.md",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    assert missing == []


def test_dockerfile_is_slim_and_has_no_baked_token() -> None:
    text = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    assert "fastapi" in text
    assert "uvicorn" in text
    assert "pydantic" in text
    assert "litellm" not in text.lower()
    assert "MATRIX_ENTERPRISE_API_TOKEN=" not in text.replace(" ", "")
    assert "matraix.enterprise.api:app" in text
    assert "USER app" in text


def test_kustomize_is_cloud_neutral_and_skips_example_secret() -> None:
    kustomization = (K8S / "kustomization.yaml").read_text(encoding="utf-8")
    assert "secret.example.yaml" not in kustomization
    blob = "\n".join(path.read_text(encoding="utf-8") for path in _text_files())
    for marker in CLOUD_MARKERS:
        assert marker not in blob, marker


def test_packaging_files_have_no_hardcoded_secrets() -> None:
    for path in _text_files():
        text = path.read_text(encoding="utf-8")
        for marker in SECRET_MARKERS:
            assert marker not in text, f"{path}: {marker}"


def test_schema_migrations_are_contiguous() -> None:
    versions = [version for version, _sql in SCHEMA_MIGRATIONS]
    assert versions == list(range(1, LATEST_SCHEMA_VERSION + 1))
    assert LATEST_SCHEMA_VERSION == 6


def test_env_example_does_not_assign_token_values() -> None:
    text = (DEPLOY / ".env.example").read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pytest.fail(f".env.example must stay commented: {stripped}")
