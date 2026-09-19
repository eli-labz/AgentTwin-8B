# Experiment control plane

Phase 3 launch records. Harbor `Job` remains the runtime. This layer **generates**
a job document and estimates cost; it does not start trials.

## Record

`EnterpriseExperiment` is an alias of `Experiment`. Required: hypothesis,
objective, tenant, organization. Defaults: `SANDBOX_ONLY`, human-validation
required, kind `baseline`.

Kinds are metadata only (`ab`, `cohort`, `model`, `prompt`, `policy`,
`latency`, `accessibility`). The same Harbor runtime executes all of them.

Governance fields (`retention_days`, `sign_off`, `notes`) live on the record.
Deletion of `jobs/` is still operator-driven.

## Harbor mapping

`map_experiment_to_harbor_job(experiment)` returns:

- `harbor_job` — `job_name`, `jobs_dir`, `n_attempts`, `environment`, `agents`, `tasks`
- `sidecar` — `experiment_id`, `tenant_id`, `seed`, policy, classification, metrics

Re-running the mapper with the same seed produces the same sidecar seed and
the same job document. Optional `persona_paths` fill agent `kwargs.persona_path`.

This does **not** import or construct `harbor.job.Job`.

## Cost estimate

`estimate_experiment_cost` is pre-run:

`trials = (sample_size or population target_size or 1) * n_attempts`  
`tokens = trials * MATRIX_ENTERPRISE_TOKENS_PER_TRIAL` (default 4000)  
`usd = tokens/1000 * MATRIX_ENTERPRISE_USD_PER_1K_TOKENS` (default 0.003)

If the estimate exceeds `execution_budget.max_cost`, `max_tokens`, or
`MATRIX_MAX_COST_USD` (when that env var is set), `decision` is `DENY_BUDGET`.
Otherwise it is the experiment’s default policy (`SANDBOX_ONLY`).

`matraix run --max-cost-usd` behavior is unchanged.

## API

See [api.md](api.md). `POST /api/v1/experiments`, `POST .../estimate`,
`GET .../harbor-job`.
