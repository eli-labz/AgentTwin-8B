# Production packaging (reference)

Phase 10 ships **cloud-neutral references**: a slim enterprise-api image,
Docker Compose, and Helm-lite kustomize. They are not a certified
multi-cloud production platform. Core domain types stay free of provider
fields. Optional provider notes live **only** in this document.

Default policy remains `SANDBOX_ONLY`. `matraix run` is unchanged. No
hard-coded secrets.

## Layout

```text
deploy/enterprise/
├── Dockerfile              # fastapi/uvicorn/pydantic/pyyaml only
├── docker-compose.yml
├── .env.example            # copy to .env; never commit tokens
└── k8s/
    ├── kustomization.yaml
    ├── namespace.yaml
    ├── configmap.yaml
    ├── deployment.yaml
    ├── service.yaml
    ├── pvc.yaml
    └── secret.example.yaml # not applied by kustomize
```

The image copies `src/matraix` + `persona/schema`. It does **not**
`pip install -e .` (that would pull LiteLLM and Harbor extras).

## Docker Compose

```bash
# From the repository root
cp deploy/enterprise/.env.example deploy/enterprise/.env
# optionally: echo 'MATRIX_ENTERPRISE_API_TOKEN=...' >> deploy/enterprise/.env
docker compose -f deploy/enterprise/docker-compose.yml up --build
curl -s http://127.0.0.1:8090/health
```

SQLite persists on the `enterprise-data` volume. Unset token = open
local/dev API (same posture as Playground).

## Kubernetes (kustomize)

```bash
docker build -f deploy/enterprise/Dockerfile -t agenttwin-enterprise-api:local .
kubectl apply -k deploy/enterprise/k8s
# Optional token (out of band):
kubectl -n agenttwin-enterprise create secret generic enterprise-api \
  --from-literal=MATRIX_ENTERPRISE_API_TOKEN="$TOKEN"
```

Manifests have **no** EKS / GKE / AKS annotations. Load balancer, ingress
class, storage class, and node selectors belong in an operator overlay.

### Optional provider notes (docs only)

| Provider | Overlay idea (not in-repo) |
|----------|----------------------------|
| Generic kube | Change `Service` to `NodePort` or add an Ingress of your choice |
| GKE | BackendConfig / managed cert in **your** overlay; do not add `cloud.google.com` here |
| EKS | ALB Ingress annotations in **your** overlay; do not add `eks.amazonaws.com` here |
| AKS | Application Gateway annotations in **your** overlay |

Remote `WorkerKind.KUBERNETES` is still a **stub**. These manifests run
the control-plane API, not a Harbor trial farm.

## CI gates

Additive workflow: [`.github/workflows/enterprise.yml`](../../.github/workflows/enterprise.yml).
Local equivalent:

```bash
bash scripts/enterprise_ci.sh
```

| Gate | What it does |
|------|----------------|
| Lint | Ruff 0.15.20 on enterprise + CLI |
| Typecheck | `compileall` + public-surface import (not a full Harbor mypy) |
| Unit / integration | `tests/unit/enterprise`, security, multitenancy, admin journey |
| Security / deps | Forbidden SDK imports + secret grep; image stays slim |
| Schema | Dimension catalog loads; persona schema tests stay in the suite |
| Migrations | Versions `1..LATEST` contiguous and idempotent |
| Container | `docker build -f deploy/enterprise/Dockerfile` |
| Smoke | `matraix smoke application/tasks/example-survey_product-feedback` |

Existing [pytest.yml](../../.github/workflows/pytest.yml) and
[ruff.yml](../../.github/workflows/ruff.yml) stay. This workflow must not
silently drop example-task smoke.

## Definition of Done — enterprise-admin journey

Master-prompt journey: tenant → population → experiment → report → audit
→ re-run → tenant isolation.

| Step | Status | Notes |
|------|--------|-------|
| Create tenant (+ default org) | **Real** | `POST /api/v1/tenants` |
| Declare / create population | **Real** | Populations + 10k-shaped declarations. 1M **materialization** is the existing Persona 1M / synthesis path, not rewritten. |
| Launch experiment | **Real (sandbox)** | Maps to a Harbor **job document**. Local worker executes the sandbox path. Does not construct `harbor.Job`. |
| Executive report | **Real** | JSON / CSV / HTML from Phase 6 artifacts. Always `synthetic_equivalent_to_human_research: false`. |
| Audit export | **Real** | Append-only table + `/api/v1/audit/export`. Not telemetry. |
| Re-run same seed | **Real (metadata)** | Same experiment id / `random_seed` can be executed again. Harbor filesystem re-run of `jobs/` is the legacy path. |
| Tenant isolation | **Real on /api/v1 + store** | `CrossTenantAccessError` / 404. Harbor `jobs/<name>/` is **not** tenant-prefixed. |
| Live IdP / JWKS | **Stub / pattern** | OIDC metadata + local HS256 `MATRIX_ENTERPRISE_OIDC_DEV_SECRET`. No live IdP. |
| SCIM directory sync | **Shaped stub** | `/api/v1/scim/Users` persists local users only. |
| Docker / K8s / queue / batch workers | **Stubs** | Catalog lists them; submit returns unavailable. |
| 1M soak / SLOs | **Not done** | Bench is a synthetic sandbox probe. |
| Certified cloud deploy | **Not done** | Compose + kustomize are references. |

Anonymous open-dev still skips RBAC so existing API/smoke stay green.
Authenticated principals cannot act on another tenant unless
`platform_admin`.

## What this package does not do

- It does not replace Playground or Harbor.
- It does not enable live model providers by default.
- It does not ship Modal / GKE credentials or cloud CRDs.
- It does not claim production readiness beyond the checklist above.
