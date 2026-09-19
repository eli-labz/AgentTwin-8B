# Governance — AgentTwin Enterprise

Governance is part of the product, not a later overlay. This document states **rules of use** for synthetic populations and experiments. Phase 0 does not automate these reviews; later phases must attach them to experiment records.

## Synthetic persona governance

- Personas are **simulation constructs**. They are not psychologically equivalent to named employees or customers.
- Do not present Playground or Harbor outputs as a substitute for usability testing, user research, accessibility testing with affected people, employee consultation, customer research, or legal review.
- Enterprise generation must **not** clone identifiable individuals unless the organization configures a lawful, approved workflow. Default path: aggregate statistics → constraint model → synthetic population → validation → simulation.
- Document extraction error and crosswalk limits (already stated for the public 1M coreset in `docs/persona/README.md`).

## Enterprise data ingestion

- Ingested HRIS/CRM/survey extracts are tenant-owned and classified (default `INTERNAL` or higher).
- Source identifiers stay in `provenance` only when policy allows; they are not required for simulation.
- Reject undocumented uploads into shared `persona/datasets/` in multi-tenant deployments (filesystem today is a single-operator tree).

## Privacy boundaries

- Missing attributes stay missing — do not impute to “complete” a person.
- Protections against reconstructing source individuals belong in Phase 2 validation (distribution tests, uniqueness caps). Limitations must appear on generated reports.
- `RESTRICTED` data must not be sent to external model providers. Phase 4 `evaluate_policy` returns `DENY` for `RESTRICTED` + external. `CONFIDENTIAL` + external requires `allow_external` and returns `ALLOW_WITH_REDACTION`.

## Model-provider exposure

- Provider choice is a **policy** input, not a persona-layer hard-code. The Phase 4 model gateway routes by catalog name; LiteLLM stays in existing persona helpers and is not imported by `matraix.enterprise`.
- Hosted third-party models may see prompt text (including persona dimensions). Classify and redact first.
- Vendor-locked CLI agents (`persona-claude-code`, `persona-gemini-cli`, `persona-codex`) inherit that vendor’s data handling — disclose this on experiments.

## Experimentation controls

- Every enterprise experiment should record hypothesis, objective, seed, populations, models, budget, and default policy (`SANDBOX_ONLY` until changed).
- Live production actions (ERP writes, real email) require policy + authorization. Prefer mocked or synthetic systems (`docs/enterprise/TARGET_ARCHITECTURE.md`).
- Budgets: warn and hard-stop. Phase 0 reuses `MATRIX_MAX_COST_USD` for host Survey/Chat only.

## Retention

- Job trees under `jobs/` persist until an operator deletes them. There is no automated retention today.
- Future `retention_policy` on `Experiment` must be enforced by the data plane, not only documented.
- Security audit records should outlive (or be separable from) routine telemetry.

## Reproducibility

- Harbor jobs already pin agent, model, task path, and persona files.
- Experiments must also pin random seed, code version, prompt/tool versions, and policy decisions (Phase 3–6).
- Re-run is a first-class action in the definition of done — not a new pipeline.

## Human validation

Simulation is for **hypotheses and potential failures**. Reports must include:

- Limitations
- Recommended human validation
- A statement that synthetic-user metrics are not equivalent to employee or customer research

Phase 6 evaluation bundles always set `synthetic_equivalent_to_human_research: false`
and `recommended_human_validation: true`. LLM judges are supplemental notes and
never the pass/fail authority. Do not present `/evaluation` JSON, hierarchical
metrics, or sandbox completions as a substitute for human research. See
[telemetry.md](telemetry.md).

## Audit

- Target: append-only log of actor, tenant, action, resource, timestamp, policy, result, trace.
- Phase 0: domain operations exist only in-process; no durable audit sink yet.

## Risk review

Before exposing an experiment to external models or production-like tools, review:

- Data classification of personas and task fixtures
- Whether the environment is sandboxed
- Cost and concurrency limits
- Whether any cohort is an adversarial/red-team **simulation category** (not a diagnosis)

Sign-off belongs on the experiment record (`ExperimentGovernance.sign_off`). Phase 3 stores it; Phase 9 hardens IAM around who may set it.
