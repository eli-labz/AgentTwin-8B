import { useCallback, useEffect, useMemo, useState } from "react";

import { FOCUS_RING } from "@/components/cockpit/cockpitShared";
import {
  FALLBACK_MANIFEST,
  HUMAN_VALIDATION_NOTE,
  SYNTHETIC_DISCLAIMER,
  downloadEnterpriseReport,
  enterpriseFetch,
  readEnterpriseSettings,
  writeEnterpriseSettings,
  type EnterpriseManifest,
} from "@/lib/enterpriseApi";

export interface EnterpriseConsoleProps {
  page: string | null;
  executionId: string | null;
  onPageChange: (page: string) => void;
  onExecutionChange: (executionId: string | null) => void;
}

type WizardDraft = {
  hypothesis: string;
  objective: string;
  task_path: string;
  sample_size: number;
  random_seed: number;
  model_name: string;
  metrics: string;
};

const EMPTY_WIZARD: WizardDraft = {
  hypothesis: "Novice users retry more often",
  objective: "Measure retry rate",
  task_path: "application/tasks/example-survey_product-feedback",
  sample_size: 2,
  random_seed: 42,
  model_name: "sandbox",
  metrics: "retry_rate",
};

function Disclaimer() {
  return (
    <aside className="mb-4 rounded-xl border border-amber-500/40 bg-amber-950/40 px-4 py-3 text-[13px] leading-relaxed text-amber-100">
      <strong className="block font-semibold">Limitation — not human research.</strong>
      {SYNTHETIC_DISCLAIMER} {HUMAN_VALIDATION_NOTE}{" "}
      <code className="text-[12px]">synthetic_equivalent_to_human_research: false</code>
    </aside>
  );
}

export function EnterpriseConsole({
  page,
  executionId,
  onPageChange,
  onExecutionChange,
}: EnterpriseConsoleProps) {
  const [manifest, setManifest] = useState<EnterpriseManifest>(FALLBACK_MANIFEST);
  const [error, setError] = useState<string | null>(null);
  const [payload, setPayload] = useState<unknown>(null);
  const [wizardStep, setWizardStep] = useState(0);
  const [draft, setDraft] = useState<WizardDraft>(EMPTY_WIZARD);
  const [settings, setSettings] = useState(readEnterpriseSettings);

  const active = page && manifest.nav.some((item) => item.id === page) ? page : "overview";

  useEffect(() => {
    enterpriseFetch<EnterpriseManifest>("/api/v1/console/manifest")
      .then(setManifest)
      .catch(() => setManifest(FALLBACK_MANIFEST));
  }, []);

  const loadList = useCallback(async (path: string) => {
    setError(null);
    try {
      setPayload(await enterpriseFetch(path));
    } catch (err) {
      setPayload(null);
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    if (active === "overview") void loadList("/api/v1/tenants");
    else if (active === "organizations") void loadList("/api/v1/organizations");
    else if (active === "populations") void loadList("/api/v1/populations");
    else if (active === "personas") void loadList("/api/v1/personas");
    else if (active === "experiments") void loadList("/api/v1/experiments");
    else if (active === "models") void loadList("/api/v1/models/catalog");
    else if (active === "evaluations") {
      void loadList("/api/v1/executions");
    }     else if (active === "analytics") {
      void loadList("/api/v1/executions");
    } else if (active === "governance") {
      void loadList("/api/v1/governance/reviews");
    } else if (active === "audit") {
      void loadList("/api/v1/audit");
    }
  }, [active, loadList, settings.tenantId, settings.token, settings.base]);

  const wizardLabels = useMemo(
    () => manifest.wizard.map((item) => item.label),
    [manifest.wizard],
  );

  const launch = async () => {
    setError(null);
    try {
      const experiment = await enterpriseFetch<{ id: string }>("/api/v1/experiments", {
        method: "POST",
        body: JSON.stringify({
          hypothesis: draft.hypothesis,
          objective: draft.objective,
          task_path: draft.task_path,
          sample_size: draft.sample_size,
          random_seed: draft.random_seed,
          model_name: draft.model_name,
          metrics: draft.metrics.split(",").map((item) => item.trim()).filter(Boolean),
        }),
      });
      const execution = await enterpriseFetch<{
        id: string;
        status: string;
        decision: string;
        result: { trace_id?: string };
      }>(`/api/v1/experiments/${experiment.id}/execute`, {
        method: "POST",
        body: JSON.stringify({ worker_kind: "local" }),
      });
      onExecutionChange(execution.id);
      setPayload({
        experiment_id: experiment.id,
        execution_id: execution.id,
        status: execution.status,
        decision: execution.decision,
        trace_id: execution.result?.trace_id,
        synthetic_equivalent_to_human_research: false,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const loadReport = async (id: string) => {
    setError(null);
    try {
      const report = await enterpriseFetch(`/api/v1/executions/${id}/report?format=json`);
      onExecutionChange(id);
      setPayload(report);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const loadArtifacts = async (id: string) => {
    setError(null);
    try {
      const [evaluation, metrics, trace, failures] = await Promise.all([
        enterpriseFetch(`/api/v1/executions/${id}/evaluation`),
        enterpriseFetch(`/api/v1/executions/${id}/metrics`),
        enterpriseFetch(`/api/v1/executions/${id}/trace`),
        enterpriseFetch(`/api/v1/failures?execution_id=${id}`),
      ]);
      onExecutionChange(id);
      setPayload({
        synthetic_equivalent_to_human_research: false,
        evaluation,
        metrics,
        trace,
        failures,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="flex min-h-0 flex-1 overflow-hidden">
      <nav
        className="custom-scrollbar w-52 shrink-0 overflow-auto border-r border-outline-dim bg-surface-lowest px-2 py-3"
        aria-label="Enterprise"
      >
        {manifest.nav.map((item) => (
          <button
            key={item.id}
            type="button"
            aria-current={item.id === active ? "page" : undefined}
            onClick={() => onPageChange(item.id)}
            className={`mb-0.5 flex w-full rounded-lg px-3 py-2 text-left text-[13px] font-medium ${FOCUS_RING} ${
              item.id === active
                ? "bg-primary text-on-primary"
                : "text-text-variant hover:bg-surface-high/70 hover:text-text-main"
            }`}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <div className="custom-scrollbar min-h-0 flex-1 overflow-auto">
        <div className="mx-auto w-full max-w-[960px] px-5 py-4">
          <h1 className="font-display text-[20px] font-bold tracking-tight text-text-main">
            {manifest.nav.find((item) => item.id === active)?.label ?? "Enterprise"}
          </h1>
          <p className="mt-1 text-[13px] text-text-variant">
            Additive console. Harbor Job is not replaced. Default policy{" "}
            {manifest.default_policy}.
          </p>
          <Disclaimer />
          {error && (
            <p className="mb-3 rounded-lg border border-red-500/40 bg-red-950/40 px-3 py-2 text-[13px] text-red-100">
              {error} — set tenant and token under Settings, then start{" "}
              <code>matraix enterprise-api</code>.
            </p>
          )}

          {active === "experiments" && (
            <section className="mb-4 rounded-xl border border-outline-dim bg-surface p-4">
              <h2 className="mb-2 text-[15px] font-semibold">Experiment wizard</h2>
              <div className="mb-3 flex flex-wrap gap-1.5">
                {wizardLabels.map((label, index) => (
                  <button
                    key={label}
                    type="button"
                    onClick={() => setWizardStep(index)}
                    className={`rounded-full px-2.5 py-1 text-[12px] ${
                      index === wizardStep
                        ? "bg-primary text-on-primary"
                        : "bg-surface-high text-text-variant"
                    }`}
                  >
                    {index + 1}. {label}
                  </button>
                ))}
              </div>
              <label className="mb-2 block text-[12px] text-text-variant">
                Hypothesis
                <textarea
                  className="mt-1 w-full rounded-lg border border-outline-dim bg-field px-3 py-2 text-[13px] text-text-main"
                  value={draft.hypothesis}
                  onChange={(event) =>
                    setDraft((prev) => ({ ...prev, hypothesis: event.target.value }))
                  }
                />
              </label>
              <label className="mb-2 block text-[12px] text-text-variant">
                Objective
                <input
                  className="mt-1 w-full rounded-lg border border-outline-dim bg-field px-3 py-2 text-[13px] text-text-main"
                  value={draft.objective}
                  onChange={(event) =>
                    setDraft((prev) => ({ ...prev, objective: event.target.value }))
                  }
                />
              </label>
              <label className="mb-2 block text-[12px] text-text-variant">
                Task path
                <input
                  className="mt-1 w-full rounded-lg border border-outline-dim bg-field px-3 py-2 text-[13px] text-text-main"
                  value={draft.task_path}
                  onChange={(event) =>
                    setDraft((prev) => ({ ...prev, task_path: event.target.value }))
                  }
                />
              </label>
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  className="rounded-lg bg-surface-high px-3 py-1.5 text-[13px]"
                  onClick={() => setWizardStep((step) => Math.max(0, step - 1))}
                >
                  Back
                </button>
                <button
                  type="button"
                  className="rounded-lg bg-surface-high px-3 py-1.5 text-[13px]"
                  onClick={() =>
                    setWizardStep((step) => Math.min(wizardLabels.length - 1, step + 1))
                  }
                >
                  Next
                </button>
                <button
                  type="button"
                  className="rounded-lg bg-primary px-3 py-1.5 text-[13px] text-on-primary"
                  onClick={() => void launch()}
                >
                  Launch sandbox
                </button>
              </div>
            </section>
          )}

          {(active === "evaluations" || active === "analytics") &&
            Array.isArray(payload) && (
              <div className="mb-3 flex flex-wrap gap-2">
                {payload.map((row) => {
                  const record = row as { id: string; status?: string };
                  return (
                    <button
                      key={record.id}
                      type="button"
                      className={`rounded-lg px-3 py-1.5 text-[12px] ${
                        executionId === record.id
                          ? "bg-primary text-on-primary"
                          : "bg-surface-high text-text-variant"
                      }`}
                      onClick={() =>
                        void (active === "analytics"
                          ? loadReport(record.id)
                          : loadArtifacts(record.id))
                      }
                    >
                      {record.id.slice(0, 18)}… {record.status}
                    </button>
                  );
                })}
              </div>
            )}

          {active === "analytics" &&
            payload &&
            !Array.isArray(payload) &&
            typeof payload === "object" &&
            "success" in (payload as object) && (
              <section className="mb-4 grid grid-cols-1 gap-3 md:grid-cols-2">
                {(["success", "risk", "cost", "confidence"] as const).map((key) => (
                  <div
                    key={key}
                    className="rounded-xl border border-outline-dim bg-surface p-3"
                  >
                    <h2 className="text-[13px] font-semibold capitalize">{key}</h2>
                    <pre className="mt-1 overflow-auto text-[11px]">
                      {JSON.stringify((payload as Record<string, unknown>)[key], null, 2)}
                    </pre>
                  </div>
                ))}
                <p className="md:col-span-2 text-[12px] text-text-variant">
                  Export:{" "}
                  {executionId &&
                    (["json", "csv", "html"] as const).map((fmt) => (
                      <button
                        key={fmt}
                        type="button"
                        className="mr-2 underline"
                        onClick={() =>
                          void downloadEnterpriseReport(
                            `/api/v1/executions/${executionId}/report?format=${fmt}`,
                            `enterprise-report.${fmt}`,
                          ).catch((err) =>
                            setError(err instanceof Error ? err.message : String(err)),
                          )
                        }
                      >
                        {fmt}
                      </button>
                    ))}
                  · synthetic_equivalent_to_human_research: false
                </p>
              </section>
            )}

          {active === "settings" && (
            <section className="mb-4 rounded-xl border border-outline-dim bg-surface p-4">
              <label className="mb-2 block text-[12px] text-text-variant">
                API base
                <input
                  className="mt-1 w-full rounded-lg border border-outline-dim bg-field px-3 py-2 text-[13px]"
                  value={settings.base}
                  onChange={(event) =>
                    setSettings((prev) => ({ ...prev, base: event.target.value }))
                  }
                />
              </label>
              <label className="mb-2 block text-[12px] text-text-variant">
                Tenant id
                <input
                  className="mt-1 w-full rounded-lg border border-outline-dim bg-field px-3 py-2 text-[13px]"
                  value={settings.tenantId}
                  onChange={(event) =>
                    setSettings((prev) => ({ ...prev, tenantId: event.target.value }))
                  }
                />
              </label>
              <label className="mb-2 block text-[12px] text-text-variant">
                Bearer token
                <input
                  type="password"
                  className="mt-1 w-full rounded-lg border border-outline-dim bg-field px-3 py-2 text-[13px]"
                  value={settings.token}
                  onChange={(event) =>
                    setSettings((prev) => ({ ...prev, token: event.target.value }))
                  }
                />
              </label>
              <button
                type="button"
                className="mt-2 rounded-lg bg-primary px-3 py-1.5 text-[13px] text-on-primary"
                onClick={() => writeEnterpriseSettings(settings)}
              >
                Save locally
              </button>
              <p className="mt-2 text-[12px] text-text-variant">
                Stored in this browser only. Never commit secrets.
              </p>
            </section>
          )}

          {manifest.stub_pages.includes(active) && (
            <p className="mb-3 text-[13px] text-text-variant">
              Placeholder page. Nav and the experiment wizard plus evaluation
              artifacts are the Phase 7 slice.
            </p>
          )}

          {payload !== null && (
            <pre className="overflow-auto rounded-xl border border-outline-dim bg-field p-3 text-[12px] text-text-main">
              {JSON.stringify(payload, null, 2)}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}
