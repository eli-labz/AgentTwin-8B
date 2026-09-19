# Executive reporting

Phase 8 builds **AgentTwin Enterprise** executive reports from Phase 6
artifacts (`evaluation`, `metrics`, `failure`, plus execution status). Reports
are composed on the fly — no new store migration. They are **not** human
research.

Every payload forces:

- `synthetic_equivalent_to_human_research: false`
- `recommended_human_validation: true`
- the required limitation notes from the evaluation and metrics SDKs

Do not present these exports as usability testing, employee consultation, or
customer research.

## Views

| Section | Source |
|---------|--------|
| Success | Pass/fail from the evaluation artifact (status fallback) |
| Risk | Failure taxonomy + denied / held / unavailable counts |
| Subgroups | Status, policy decision, worker kind |
| Cost | Token, USD, and latency points on the metrics artifact |
| Confidence | Normal-approximation 95% CI on pass rate (`n >= 2`) |
| Reproducibility | Seed, code version, `trace_id`s |
| Drill-down | One row per execution |

Schema: `EnterpriseExecutiveReport.v1` (`matraix.enterprise.reporting`).

## Exports

| Format | Media type | Notes |
|--------|------------|--------|
| `json` (default) | `application/json` | Full document |
| `csv` | `text/csv` | Section / key / value rows + limitations |
| `html` | `text/html` | PDF-ready (`@page A4`). Print to PDF. |

```bash
# After a local sandbox execute
curl -sS -H "X-Tenant-Id: $TENANT" \
  "http://127.0.0.1:8090/api/v1/executions/$EXEC/report?format=json"
curl -sS -H "X-Tenant-Id: $TENANT" \
  "http://127.0.0.1:8090/api/v1/experiments/$EXP/report?format=html" -o report.html
```

Tenant isolation matches the rest of `/api/v1`: missing or foreign ids are
`404`. Bravo cannot read Alpha’s report.

## Existing export paths (kept)

Phase 8 **extends** them; it does not replace them.

| Surface | What changed |
|---------|----------------|
| `matraix results` | Additive `html` format; JSON/CSV/text now carry limitations + `syntheticEquivalentToHumanResearch: false` |
| Playground **Download PDF** | Footer line: synthetic outputs are not human research. jsPDF path unchanged |
| `/console` Analytics + Playground Enterprise Analytics | Success / risk / cost / confidence cards + JSON/CSV/HTML download (tenant headers) |

```bash
uv run matraix results <job_name> --format json,csv,html -o /tmp/matraix-exports/
```

Harbor job trees stay `MatraixJobResults.v1`. Enterprise execution reports stay
`EnterpriseExecutiveReport.v1`. The CLI does not import `matraix.enterprise`.

`SANDBOX_ONLY` and `matraix run` defaults are unchanged.
