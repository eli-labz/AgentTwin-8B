# Security

AgentTwin Enterprise / MatrAIx is a simulation and evaluation platform. This document describes **current** practices and **target** enterprise controls. Implementation status is called out explicitly.

## Current practices (this repository)

- **No production secrets in git.** Model and cloud credentials are read from environment variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DASHSCOPE_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `XAI_API_KEY`, `MODAL_TOKEN_*`, `USE_COMPUTER_API_KEY`, and others documented in `docs/environment/agents.md`).
- **Credential preflight does not print secret values** (`matraix.provider_credentials`).
- **Playground local API is unauthenticated.** Treat `POST /api/harbor/jobs` as a trusted-operator interface. Enterprise `/api/v1` adds optional Bearer / OIDC JWT, RBAC, CSRF on cookie sessions, and rate limits (Phase 9).
- **Harbor Viewer / registry** may use GitHub OAuth (Supabase). That is not tenant IAM. Default Supabase URL and publishable key are compiled into `harbor/auth/constants.py`.
- **Remote Runner** (`REMOTE_RUNNER_API_KEY`) is client-sent only; the worker HTTP server does not currently reject unauthenticated calls.
- **Task network policy** can deny or allow-list egress (`harbor.models.task.config.NetworkMode`).
- **Spend gate** `MATRIX_MAX_COST_USD` can stop further host Survey/Chat provider calls (`playground.budget`).
- **License:** MIT (`LICENSE`).

If you discover a vulnerability in a deployment of this software, do not file a public issue with secrets or customer data. Contact the repository maintainers privately.

## Enterprise target (see `docs/enterprise/SECURITY_MODEL.md`)

- Tenant isolation on every enterprise-owned record
- Policy decisions on every consequential action (default `SANDBOX_ONLY`)
- Data classification: PUBLIC / INTERNAL / CONFIDENTIAL / RESTRICTED
- Secrets abstraction; encryption-ready persistence
- OIDC/OAuth/SAML-compatible SSO; RBAC + optional ABAC
- Restricted admin APIs, CSRF protections, secure cookies, API authorization
- Input validation, output encoding, rate limiting, dependency scanning
- Container isolation and least privilege (Harbor environments)
- Append-only audit log, separable from normal telemetry
- Never place secrets in model prompts or logs

## Phase 0–9 code

`matraix.enterprise` enforces tenant-scoped IDs and fails closed on cross-tenant repository access. `/api/v1` binds `X-Tenant-Id` and optionally `MATRIX_ENTERPRISE_API_TOKEN`. Authenticated principals are RBAC-checked; cookie sessions require CSRF; the audit log is append-only and separate from telemetry. Harbor `jobs/` on disk is still not tenant-prefixed. See `docs/enterprise/identity.md`.

## Reporting

Prefer a private maintainer channel. Do not attach live API keys, persona datasets with real identifiers, or customer artifacts to public trackers.
