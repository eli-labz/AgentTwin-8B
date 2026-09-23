# Governance — AgentTwin Enterprise

How decisions, reviews and controls work for this repository and for deployments of the
enterprise control plane. Security reporting lives in [SECURITY.md](SECURITY.md); the attacker
view lives in [docs/enterprise/THREAT_MODEL.md](docs/enterprise/THREAT_MODEL.md).

## 1. The governing principle

**Simulated users are controlled experimental instruments.** They support hypothesis generation,
stress testing, comparative evaluation, subgroup analysis, usability, safety and workflow testing.

They **must not** be presented as validated replacements for empirical evidence from real human
populations. Every artifact the platform produces carries the provenance needed to judge it:
population construction method, source datasets, sampling strategy, seed, model configuration,
generator version, validation findings, and an explicit validity classification
(`SIMULATION_ONLY`, `CALIBRATED_TO_EXTERNAL_DATA`, `HUMAN_VALIDATED`). The default is
`SIMULATION_ONLY`, and nothing promotes itself out of that class automatically.

## 2. Roles and separation of duties

| Role | May | May not |
|------|-----|---------|
| Viewer | Read populations, experiments, runs, metrics, reports | Change anything |
| Analyst | Viewer + read artifacts | Create or launch work |
| Researcher | Define populations, materialize them, define and launch experiments | Approve its own experiment, manage identities, read audit |
| Operator | Researcher + approve, control runs, manage models, budgets, policies, read audit | Manage identities |
| Admin | Operator + manage tenants, organizations, users, service accounts | — |

Approval is a **separate permission** from launch, so a deployment can require a second person
for consequential work. The full matrix is served at `GET /api/v1/roles`.

## 3. Data governance

- **Privacy-preserving by default.** Enterprise workforce modelling defaults to
  `aggregate_stats_then_synthetic`: derive distributions from aggregate statistics, then
  synthesize a population. Modes that would clone identifiable employees into synthetic replicas
  are **rejected at validation**, not merely discouraged.
- **Personas are referenced, never copied into tasks.** Population versions are immutable and
  content-hashed; a result always maps to the exact population it ran against.
- **Classification travels with records.** `PUBLIC` / `INTERNAL` / `CONFIDENTIAL` / `RESTRICTED`
  gate which model configurations may process them.
- **Composition views are aggregate.** Dashboards receive subgroup counts and bounded dimension
  summaries, never full 1,290-dimension records, so a dashboard cannot become an unnecessary
  disclosure.

## 4. Secrets

Credentials are read through a secret-provider interface, never inline at the point of use, and
are never written to database records, job YAML, logs, traces, audit rows or artifacts. Service
account tokens are stored only as peppered hashes; a raw token is shown exactly once. A redaction
filter covers logging, and audit details are redacted on write.

## 5. Audit and accountability

The audit stream is append-only and hash-chained per tenant, with no update or delete surface.
Authentication, resource creation and mutation, experiment launch, policy decisions, approvals,
**denials** and failed privileged operations are all recorded. `GET /api/v1/audit-events/verify`
recomputes the chain and reports the first break, so tampering is detectable even by someone with
database access. Append-only *storage* is a deployment responsibility.

## 6. Change control for this repository

- Research capability is never deleted to make room for enterprise features; enterprise concerns
  are additive and backward compatible.
- Significant decisions are recorded as ADRs under `docs/adr/`, including alternatives rejected.
- Abstractions are introduced only where a concrete implementation uses them.
- Continuous integration runs lint and the curated test suite. Tenant isolation, reproducibility
  and security suites are first-class and must pass.
- Claims in documentation must match verified behaviour. A capability is described as implemented
  only when it is implemented and tested; planned work is labelled planned.

## 7. Reporting honesty

Every report states its sample size, whether metrics are weighted, its validity classification,
and its limitations, including a recommendation for human validation where conclusions would
otherwise be over-read. Comparative results expose differences without claiming validated human
causal effects.
