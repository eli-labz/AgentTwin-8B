# Model and policy gateways

Phase 4 adds a **provider-independent model gateway** beside LiteLLM and a
**policy gateway** that every enterprise execution must pass. Persona prompt
code does not import this module to call a vendor SDK. Existing LiteLLM usage
in `matraix.persona_grounding` and `matraix.persona_agent_context` is unchanged.

## Policy gateway

`evaluate_policy(request, policy=None)` returns a `PolicyEvaluation`.

| Decision | When |
|----------|------|
| `DENY` | Forbidden action, deny-listed / not-allow-listed provider, `RESTRICTED` + external |
| `ALLOW_WITH_APPROVAL` | Consequential live action (`write_crm`, `write_erp`, `send_email`, `deploy`, …) with `allow_live` |
| `ALLOW_WITH_REDACTION` | `CONFIDENTIAL` + external + `allow_external` |
| `ALLOW` | `PUBLIC` + external + explicit `allow_external` (and allow-list if set) |
| `SANDBOX_ONLY` | **Default.** Internal data, no override, or live writes without `allow_live` |

`default_simulation_decision` still always returns `SANDBOX_ONLY`.

Env overlays (never secrets):

| Variable | Effect |
|----------|--------|
| `MATRIX_ENTERPRISE_MODEL_ALLOWLIST` | Comma-separated provider names |
| `MATRIX_ENTERPRISE_MODEL_DENYLIST` | Comma-separated provider names |
| `MATRIX_ENTERPRISE_MODEL_RESIDENCY` | Required residency (`us`, `eu`, `local`) |
| `MATRIX_ENTERPRISE_ALLOW_EXTERNAL` | `1` / `true` to permit the ALLOW / redaction paths |
| `MATRIX_ENTERPRISE_ALLOW_LIVE` | `1` / `true` to permit approval-gated live actions |

## Model gateway

Types: `ModelProvider`, `ModelRequest`, `ModelResponse`, `ModelCapabilities`,
`ModelUsage`, `ModelPolicy`.

Routing (`route_model`) filters the named catalog by capability, allow-list,
deny-list, residency, `max_cost_score`, `max_latency_ms`, and task complexity
(`simple` / `standard` / `complex`). Provider logic stays out of persona code.

The catalog names providers only. `credential_env` is an environment **variable
name** (same idea as `matraix.provider_credentials`). No SDK is imported.

`complete_model`:

- Always evaluates policy first.
- `DENY` raises `PolicyDeniedError`.
- `ALLOW_WITH_APPROVAL` without `approved` raises `ApprovalRequiredError`.
- `SANDBOX_ONLY` / redaction use an in-process mock (`[sandbox:…]`).
- `ALLOW` selects a catalog provider and is a **dry-run** unless a completer
  is injected. LiteLLM is not called from this package.

## Persistence

One `ModelPolicy` per tenant (`tenant_model_policies`, migration v4). Missing
rows reconstruct the sandbox default. `GET`/`PUT /api/v1/model-policy`.

## API

See [api.md](api.md): `/api/v1/policy/evaluate`, `/api/v1/models/route`,
`/api/v1/models/complete`, `/api/v1/models/catalog`, `/api/v1/model-policy`.
