# Identity, authorization, and audit

Phase 9 hardens the enterprise control plane. It does **not** require a live
identity provider in CI. `matraix run` and `SANDBOX_ONLY` defaults are
unchanged. Secrets stay in the environment.

## OIDC / SSO pattern

Documented browser flow: Authorization Code + PKCE.

| Env | Purpose |
|-----|---------|
| `MATRIX_ENTERPRISE_OIDC_ISSUER` | Expected `iss` |
| `MATRIX_ENTERPRISE_OIDC_AUDIENCE` | Expected `aud` |
| `MATRIX_ENTERPRISE_OIDC_JWKS_URI` | RS256 JWKS URL (documented; local validation is HS256) |
| `MATRIX_ENTERPRISE_OIDC_DEV_SECRET` | HMAC for local/CI JWTs — never commit |

`GET /api/v1/auth/oidc` returns issuer, authorize/token endpoints, PKCE, and
`live_idp_required: false`. `POST /api/v1/auth/dev-token` mints an HS256 JWT
only when the dev secret is set and the process is **not** production.

## SCIM-shaped hooks

Not a full SCIM 2.0 server. Enough to provision a tenant user:

- `POST /api/v1/scim/Users`
- `GET /api/v1/scim/Users`
- `GET /api/v1/scim/Users/{id}`

Payload uses `userName`, `emails`, `roles`, `active`, `externalId`.

## RBAC + ABAC

Roles: `platform_admin`, `tenant_admin`, `simulation_admin`, `researcher`,
`evaluator`, `developer`, `auditor`, `viewer`.

Anonymous local/dev requests (no JWT, no session, no API token) skip RBAC so
existing smoke/API tests stay green. Authenticated principals are checked.

ABAC hooks: tenant match (non-platform admins cannot act on another tenant);
inactive principals are denied; `RESTRICTED` resources need a policy-write
role.

## Sessions, CSRF, cookies

`POST /api/v1/auth/session` sets `matrix_enterprise_session` (HttpOnly,
SameSite=Lax, Secure in production). `GET /api/v1/auth/csrf` sets
`matrix_enterprise_csrf`. Unsafe methods that carry the session cookie must
send `X-CSRF-Token`. Bearer-only clients are not cookie-CSRF scoped.

## Rate limits

Off unless `MATRIX_ENTERPRISE_RATE_LIMIT` is set or
`MATRIX_ENTERPRISE_ENV=production`. Sensitive POSTs (tenants, execute,
complete, SCIM, session) use
`MATRIX_ENTERPRISE_SENSITIVE_RATE_LIMIT`. Exceeding the window returns `429`.

## Audit log

Append-only, **not** a Phase 6 telemetry artifact. SQLite triggers abort
`UPDATE` / `DELETE`. API: `GET /api/v1/audit` and `GET /api/v1/audit/export`.
Cross-tenant list is empty / foreign user get is `CrossTenantAccessError`.

## Governance reviews

`POST/GET /api/v1/governance/reviews` for `ingestion`, `retention`, and
`model_provider_exposure`. Console Governance and Audit pages consume these
routes. See [GOVERNANCE.md](GOVERNANCE.md).

## Dependency scanning

No new CI job in this phase. Operators should run `pip-audit` or
`uv pip compile --generate-hashes` against the lockfile before production.
A unit test asserts enterprise auth modules do not contain hard-coded key
material.
