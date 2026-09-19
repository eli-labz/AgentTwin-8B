"""Executive reports from Phase 6 artifacts.

Always includes limitations and recommended human validation. Synthetic
outputs are never labeled as equivalent to human research.
"""

from __future__ import annotations

import csv
import html
import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from matraix.enterprise.evaluation import (
    HUMAN_VALIDATION_NOTE,
    SYNTHETIC_EVALUATION_LIMITATION,
)
from matraix.enterprise.ids import ExecutionId, ExperimentId, TenantId
from matraix.enterprise.metrics import SYNTHETIC_METRIC_LIMITATION
from matraix.enterprise.runtime import ExecutionRecord

SCHEMA_VERSION = "EnterpriseExecutiveReport.v1"

REQUIRED_LIMITATIONS = (
    SYNTHETIC_EVALUATION_LIMITATION,
    SYNTHETIC_METRIC_LIMITATION,
    HUMAN_VALIDATION_NOTE,
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content(artifacts: Iterable[dict[str, Any]], kind: str) -> dict[str, Any]:
    for item in artifacts:
        if item.get("kind") == kind:
            payload = item.get("content")
            return dict(payload) if isinstance(payload, dict) else {}
    return {}


@dataclass
class ExecutiveReport:
    tenant_id: TenantId
    scope: str
    experiment_id: ExperimentId | None = None
    execution_ids: tuple[str, ...] = ()
    generated_at: str = field(default_factory=_utcnow)
    success: dict[str, Any] = field(default_factory=dict)
    risk: dict[str, Any] = field(default_factory=dict)
    subgroups: list[dict[str, Any]] = field(default_factory=list)
    cost: dict[str, Any] = field(default_factory=dict)
    confidence: dict[str, Any] = field(default_factory=dict)
    reproducibility: dict[str, Any] = field(default_factory=dict)
    drill_down: list[dict[str, Any]] = field(default_factory=list)
    limitations: tuple[str, ...] = REQUIRED_LIMITATIONS
    recommended_human_validation: bool = True

    def to_dict(self) -> dict[str, Any]:
        limits = list(self.limitations)
        for required in REQUIRED_LIMITATIONS:
            if required not in limits:
                limits.append(required)
        return {
            "schema_version": SCHEMA_VERSION,
            "tenant_id": str(self.tenant_id),
            "scope": self.scope,
            "experiment_id": self.experiment_id.value if self.experiment_id else None,
            "execution_ids": list(self.execution_ids),
            "generated_at": self.generated_at,
            "success": dict(self.success),
            "risk": dict(self.risk),
            "subgroups": [dict(item) for item in self.subgroups],
            "cost": dict(self.cost),
            "confidence": dict(self.confidence),
            "reproducibility": dict(self.reproducibility),
            "drill_down": [dict(item) for item in self.drill_down],
            "limitations": limits,
            "recommended_human_validation": True,
            "synthetic_equivalent_to_human_research": False,
        }


def _row_from_execution(
    record: ExecutionRecord,
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    evaluation = _content(artifacts, "evaluation")
    metrics = _content(artifacts, "metrics")
    failure = _content(artifacts, "failure")
    passed = bool(evaluation.get("passed")) if evaluation else record.status.value == "completed"
    points = list(metrics.get("points") or [])
    tokens = 0.0
    cost = 0.0
    latency = 0.0
    for point in points:
        name = point.get("name")
        value = float(point.get("value") or 0)
        if name == "tokens":
            tokens += value
        elif name == "cost_usd":
            cost += value
        elif name == "latency_ms":
            latency = value
    result = dict(record.result or {})
    return {
        "execution_id": record.id.value,
        "experiment_id": record.experiment_id.value,
        "status": record.status.value,
        "decision": record.decision.value,
        "passed": passed,
        "failure": failure.get("classification") or result.get("failure"),
        "tokens": tokens,
        "cost_usd": cost,
        "latency_ms": latency,
        "trace_id": result.get("trace_id"),
        "seed": result.get("seed") or evaluation.get("seed"),
        "code_version": result.get("code_version") or evaluation.get("code_version"),
        "segments": {
            "status": record.status.value,
            "policy": record.decision.value,
            "worker": record.worker_kind.value,
        },
        "ci95": (metrics.get("aggregates") or [{}])[0].get("ci95")
        if metrics.get("aggregates")
        else None,
    }


def build_executive_report(
    *,
    tenant_id: TenantId,
    records: list[ExecutionRecord],
    artifacts_by_execution: dict[str, list[dict[str, Any]]],
    experiment_id: ExperimentId | None = None,
    scope: str | None = None,
) -> ExecutiveReport:
    rows = [
        _row_from_execution(record, artifacts_by_execution.get(record.id.value, []))
        for record in records
    ]
    n = len(rows)
    passed = sum(1 for row in rows if row["passed"])
    failed = n - passed
    failures: dict[str, int] = {}
    for row in rows:
        if row["failure"]:
            failures[str(row["failure"])] = failures.get(str(row["failure"]), 0) + 1
    tokens = sum(float(row["tokens"]) for row in rows)
    cost = sum(float(row["cost_usd"]) for row in rows)
    subgroups: dict[tuple[str, str], dict[str, float]] = {}
    for row in rows:
        for key, value in row["segments"].items():
            bucket = subgroups.setdefault((key, str(value)), {"n": 0, "passed": 0})
            bucket["n"] += 1
            bucket["passed"] += 1 if row["passed"] else 0
    subgroup_rows = [
        {
            "segment": key,
            "value": value,
            "n": int(stats["n"]),
            "success_rate": stats["passed"] / stats["n"] if stats["n"] else 0.0,
        }
        for (key, value), stats in sorted(subgroups.items())
    ]
    ci = None
    if n >= 2:
        values = [1.0 if row["passed"] else 0.0 for row in rows]
        mean = sum(values) / n
        variance = sum((item - mean) ** 2 for item in values) / (n - 1)
        half = 1.96 * (variance**0.5) / (n**0.5) if variance else 0.0
        ci = [max(0.0, mean - half), min(1.0, mean + half)]
    first = rows[0] if rows else {}
    return ExecutiveReport(
        tenant_id=tenant_id,
        scope=scope or ("experiment" if experiment_id else "execution"),
        experiment_id=experiment_id,
        execution_ids=tuple(row["execution_id"] for row in rows),
        success={
            "n": n,
            "passed": passed,
            "failed": failed,
            "rate": (passed / n) if n else 0.0,
        },
        risk={
            "failed": failed,
            "failure_classes": failures,
            "denied": sum(1 for row in rows if row["status"] == "denied"),
            "held": sum(1 for row in rows if row["status"] == "held"),
            "unavailable": sum(1 for row in rows if row["status"] == "unavailable"),
        },
        subgroups=subgroup_rows,
        cost={
            "tokens": tokens,
            "cost_usd": cost,
            "mean_latency_ms": (
                sum(float(row["latency_ms"]) for row in rows) / n if n else 0.0
            ),
        },
        confidence={
            "n": n,
            "ci95": ci,
            "note": "Normal-approximation 95% CI on pass rate; omitted when n < 2.",
        },
        reproducibility={
            "seed": first.get("seed"),
            "code_version": first.get("code_version"),
            "trace_ids": [row.get("trace_id") for row in rows if row.get("trace_id")],
        },
        drill_down=rows,
    )


def format_report_json(report: ExecutiveReport) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n"


def format_report_csv(report: ExecutiveReport) -> str:
    payload = report.to_dict()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["section", "key", "value"])
    writer.writerow(["meta", "schema_version", payload["schema_version"]])
    writer.writerow(["meta", "tenant_id", payload["tenant_id"]])
    writer.writerow(["meta", "scope", payload["scope"]])
    writer.writerow(["meta", "experiment_id", payload["experiment_id"] or ""])
    writer.writerow(
        ["meta", "synthetic_equivalent_to_human_research", "false"]
    )
    writer.writerow(["meta", "recommended_human_validation", "true"])
    for key, value in payload["success"].items():
        writer.writerow(["success", key, value])
    writer.writerow(["risk", "failed", payload["risk"]["failed"]])
    for name, count in payload["risk"]["failure_classes"].items():
        writer.writerow(["risk", name, count])
    for item in payload["subgroups"]:
        writer.writerow(
            [
                "subgroup",
                f"{item['segment']}={item['value']}",
                f"n={item['n']};success_rate={item['success_rate']}",
            ]
        )
    for key, value in payload["cost"].items():
        writer.writerow(["cost", key, value])
    writer.writerow(["confidence", "ci95", payload["confidence"]["ci95"]])
    for note in payload["limitations"]:
        writer.writerow(["limitations", "note", note])
    writer.writerow(["limitations", "human_validation", HUMAN_VALIDATION_NOTE])
    return buf.getvalue()


def format_report_html(report: ExecutiveReport) -> str:
    payload = report.to_dict()
    cards = (
        ("Success", payload["success"]),
        ("Risk", payload["risk"]),
        ("Cost", payload["cost"]),
        ("Confidence", payload["confidence"]),
    )
    card_html = []
    for title, body in cards:
        card_html.append(
            "<section class='card'><h2>"
            + html.escape(title)
            + "</h2><pre>"
            + html.escape(json.dumps(body, indent=2))
            + "</pre></section>"
        )
    subgroups = "".join(
        "<li>"
        + html.escape(
            f"{item['segment']}={item['value']} n={item['n']} "
            f"success={item['success_rate']:.2f}"
        )
        + "</li>"
        for item in payload["subgroups"]
    )
    limitations = "".join(
        f"<li>{html.escape(note)}</li>" for note in payload["limitations"]
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>AgentTwin Enterprise report</title>
  <style>
    @page {{ size: A4; margin: 16mm; }}
    body {{ font: 13px/1.45 system-ui, sans-serif; color: #111; margin: 24px; }}
    h1 {{ font-size: 20px; margin-bottom: 4px; }}
    .banner {{ background: #fff3cd; border: 1px solid #f0c36d; padding: 10px 12px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    .card {{ border: 1px solid #ddd; padding: 10px 12px; break-inside: avoid; }}
    pre {{ white-space: pre-wrap; }}
    @media print {{ .noprint {{ display: none; }} }}
  </style>
</head>
<body>
  <h1>AgentTwin Enterprise executive report</h1>
  <p>Scope {html.escape(payload["scope"])} · tenant {html.escape(payload["tenant_id"])} ·
     generated {html.escape(payload["generated_at"])}</p>
  <div class="banner">
    <strong>Limitations — not human research.</strong>
    <p>synthetic_equivalent_to_human_research: false</p>
    <p>recommended_human_validation: true</p>
    <ul>{limitations}</ul>
  </div>
  <div class="grid">{"".join(card_html)}</div>
  <section class="card"><h2>Subgroup differences</h2><ul>{subgroups or "<li>none</li>"}</ul></section>
  <p class="noprint">Print this page for a PDF-ready export. Harbor Playground PDF paths are unchanged.</p>
</body>
</html>
"""


def report_from_runtime(
    runtime: Any,
    *,
    tenant_id: TenantId,
    execution_id: ExecutionId | None = None,
    experiment_id: ExperimentId | None = None,
) -> ExecutiveReport:
    """Load tenant-scoped executions and artifacts, then compose a report."""
    if execution_id is not None:
        record = runtime.execution.get(tenant_id, execution_id)
        artifacts = [
            item.to_dict()
            for item in runtime.data.list_artifacts(tenant_id, execution_id=record.id)
        ]
        return build_executive_report(
            tenant_id=tenant_id,
            records=[record],
            artifacts_by_execution={record.id.value: artifacts},
            experiment_id=record.experiment_id,
            scope="execution",
        )
    if experiment_id is None:
        raise ValueError("execution_id or experiment_id is required")
    runtime.store.get_experiment(tenant_id, experiment_id)
    records = [
        item
        for item in runtime.execution.list(tenant_id)
        if item.experiment_id == experiment_id
    ]
    artifacts_by_execution = {
        record.id.value: [
            item.to_dict()
            for item in runtime.data.list_artifacts(tenant_id, execution_id=record.id)
        ]
        for record in records
    }
    return build_executive_report(
        tenant_id=tenant_id,
        records=records,
        artifacts_by_execution=artifacts_by_execution,
        experiment_id=experiment_id,
        scope="experiment",
    )


def render_report(report: ExecutiveReport, fmt: str) -> tuple[str, str]:
    kind = (fmt or "json").strip().lower()
    if kind == "csv":
        return format_report_csv(report), "text/csv; charset=utf-8"
    if kind in {"html", "pdf-html", "pdf"}:
        return format_report_html(report), "text/html; charset=utf-8"
    if kind in {"json", ""}:
        return format_report_json(report), "application/json"
    raise ValueError(f"unsupported report format {fmt!r}")
