#!/usr/bin/env bash
# Additive enterprise CI gates (Phase 10). Does not replace pytest.yml / ruff.yml.
# Run from the repository root:
#   bash scripts/enterprise_ci.sh
#
# Gates: lint, typecheck (compileall + public import), unit/integration,
# security/deps, schema + migration validation, container build (if Docker),
# smoke simulation. Does not change matraix run defaults.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
if command -v uv >/dev/null 2>&1; then
  PY=(uv run python)
  PYTEST=(uv run pytest)
  RUFF=(uvx ruff@0.15.20)
else
  PY=("$PYTHON")
  PYTEST=("$PYTHON" -m pytest)
  RUFF=(ruff)
fi

export PYTHONPATH="${PYTHONPATH:-}:.:src:environment/runtime:packages/playground/src:application/playground"

echo "== lint (enterprise tree) =="
"${RUFF[@]}" check src/matraix/enterprise tests/unit/enterprise tests/security tests/multitenancy tests/integration/enterprise src/matraix/cli.py

echo "== typecheck (compileall + public surface) =="
"${PY[@]}" -m compileall -q src/matraix/enterprise src/matraix/cli.py
"${PY[@]}" - <<'PY'
from matraix.enterprise import (
    BENCHMARK_SCHEMA,
    PolicyDecision,
    run_benchmark,
    worker_catalog,
)
assert BENCHMARK_SCHEMA == "EnterpriseBenchmark.v1"
assert PolicyDecision.SANDBOX_ONLY.value == "SANDBOX_ONLY"
assert any(item["kind"] == "local" for item in worker_catalog())
report = run_benchmark(personas=1, tasks=1)
assert report.default_policy == "SANDBOX_ONLY"
print("public surface import: ok")
PY

echo "== schema + migration validation =="
"${PY[@]}" - <<'PY'
from matraix.enterprise.migrations import LATEST_SCHEMA_VERSION, SCHEMA_MIGRATIONS, apply_migrations
import sqlite3

versions = [version for version, _sql in SCHEMA_MIGRATIONS]
assert versions == list(range(1, LATEST_SCHEMA_VERSION + 1)), versions
conn = sqlite3.connect(":memory:")
applied = apply_migrations(conn)
assert applied == versions
assert apply_migrations(conn) == []
conn.close()

from matraix.enterprise.persona_schema import load_dimension_catalog
catalog = load_dimension_catalog()
assert "age_bracket" in catalog
print(f"schema migrations 1..{LATEST_SCHEMA_VERSION} + dimension catalog: ok")
PY

echo "== security / deps =="
"${PY[@]}" - <<'PY'
from pathlib import Path
import ast
import sys

root = Path("src/matraix/enterprise")
banned = {"litellm", "harbor", "docker", "kubernetes", "boto3", "google.cloud"}
for path in root.rglob("*.py"):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module.split(".")[0]]
        else:
            continue
        hit = banned.intersection(names)
        if hit:
            sys.exit(f"{path}: forbidden import {sorted(hit)}")

needles = ("sk-ant-", "sk-proj-", "BEGIN PRIVATE KEY", "AKIA", "ghp_")
for path in [*root.rglob("*.py"), *Path("deploy/enterprise").rglob("*")]:
    if not path.is_file() or path.suffix in {".pyc"}:
        continue
    text = path.read_text(encoding="utf-8", errors="ignore")
    for needle in needles:
        if needle in text:
            sys.exit(f"{path}: looks like a hard-coded secret ({needle})")
    if path.name == "Dockerfile" and "MATRIX_ENTERPRISE_API_TOKEN=" in text.replace(" ", ""):
        sys.exit(f"{path}: must not bake MATRIX_ENTERPRISE_API_TOKEN")
print("enterprise import + secret scan: ok")
PY

echo "== unit / integration / security / multitenancy =="
"${PYTEST[@]}" \
  tests/unit/enterprise \
  tests/security \
  tests/multitenancy \
  tests/integration/enterprise \
  -q --tb=short

echo "== smoke simulation (example survey; no API key) =="
if command -v uv >/dev/null 2>&1; then
  uv run matraix smoke application/tasks/example-survey_product-feedback
else
  "${PY[@]}" -m matraix smoke application/tasks/example-survey_product-feedback || \
    "${PY[@]}" -c "from matraix.cli import main; main(['smoke', 'application/tasks/example-survey_product-feedback'])"
fi

echo "== synthetic benchmark (CI-sized) =="
if command -v uv >/dev/null 2>&1; then
  uv run matraix enterprise-bench --personas 5 --tasks 1 --format json
else
  "${PY[@]}" -c "from matraix.cli import main; main(['enterprise-bench', '--personas', '5', '--tasks', '1', '--format', 'json'])"
fi

echo "== container build (optional) =="
if [[ "${ENTERPRISE_CI_SKIP_DOCKER:-}" == "1" ]]; then
  echo "ENTERPRISE_CI_SKIP_DOCKER=1; image build deferred to the workflow job"
elif command -v docker >/dev/null 2>&1; then
  docker build -f deploy/enterprise/Dockerfile -t agenttwin-enterprise-api:ci .
  echo "docker build: ok"
else
  echo "docker not available; skipping image build (workflow runs it on GitHub)"
fi

echo "enterprise CI gates: ok"
