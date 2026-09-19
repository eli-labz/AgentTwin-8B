"""Synthetic EnterpriseBenchmark.v1 harness."""

from __future__ import annotations

import json

from matraix.cli import main
from matraix.enterprise import BENCHMARK_SCHEMA, run_benchmark
from matraix.enterprise.benchmarks import format_benchmark_json, format_benchmark_text


def test_benchmark_modules_do_not_import_harbor_or_providers() -> None:
    import matraix.enterprise.benchmarks as benchmarks

    assert "harbor" not in benchmarks.__dict__
    assert "litellm" not in benchmarks.__dict__
    assert "docker" not in benchmarks.__dict__
    assert "kubernetes" not in benchmarks.__dict__


def test_run_benchmark_memory_reports_required_fields() -> None:
    report = run_benchmark(personas=3, tasks=1, store="memory")
    payload = report.to_dict()
    assert payload["schema_version"] == BENCHMARK_SCHEMA
    assert payload["schema_version"] == "EnterpriseBenchmark.v1"
    assert payload["personas"] == 3
    assert payload["tasks"] == 1
    assert payload["store"] == "memory"
    assert payload["personas_per_sec"] > 0
    assert payload["tasks_per_sec"] > 0
    assert payload["queue_latency_ms"] >= 0
    assert payload["queue_stub_reject_ms"] >= 0
    assert payload["model_latency_ms"] >= 0
    assert payload["db_latency_ms"] >= 0
    assert payload["telemetry_overhead_ms"] >= 0
    assert payload["rss_mb"] > 0
    assert payload["cpu_user_s"] >= 0
    assert payload["cost_per_persona_usd"] >= 0
    assert payload["cost_per_task_usd"] >= 0
    assert payload["duration_s"] > 0
    assert payload["default_policy"] == "SANDBOX_ONLY"
    assert payload["synthetic_equivalent_to_human_research"] is False
    assert any("not a 1M soak" in note for note in payload["notes"])


def test_run_benchmark_sqlite_and_formats(tmp_path) -> None:
    path = tmp_path / "bench.sqlite"
    report = run_benchmark(personas=2, tasks=1, store="sqlite", db_path=str(path))
    assert report.store == "sqlite"
    text = format_benchmark_text(report)
    assert "Enterprise benchmark" in text
    assert "synthetic_equivalent_to_human_research: false" in text
    parsed = json.loads(format_benchmark_json(report))
    assert parsed["store"] == "sqlite"
    assert parsed["personas"] == 2


def test_cli_enterprise_bench_json(capsys, tmp_path) -> None:
    out = tmp_path / "bench.json"
    main(
        [
            "enterprise-bench",
            "--personas",
            "2",
            "--tasks",
            "1",
            "--format",
            "json",
            "-o",
            str(out),
        ]
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "EnterpriseBenchmark.v1"
    assert payload["synthetic_equivalent_to_human_research"] is False
    assert "enterprise-bench: wrote" in capsys.readouterr().err
