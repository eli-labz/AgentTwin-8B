/** Client for the additive AgentTwin Enterprise `/api/v1` control plane. */

export const SYNTHETIC_DISCLAIMER =
  "Synthetic persona outputs are simulation parameters, not equivalent to human research, usability testing, or employee consultation.";

export const HUMAN_VALIDATION_NOTE =
  "Recommended human validation before operational or research claims.";

export interface EnterpriseNavItem {
  id: string;
  label: string;
}

export interface EnterpriseManifest {
  nav: EnterpriseNavItem[];
  wizard: EnterpriseNavItem[];
  limitation: string;
  recommended_human_validation: boolean;
  synthetic_equivalent_to_human_research: boolean;
  default_policy: string;
  stub_pages: string[];
}

const STORAGE = {
  base: "enterprise.apiBase",
  tenant: "enterprise.tenantId",
  token: "enterprise.token",
} as const;

export function defaultEnterpriseApiBase(): string {
  const fromEnv = (import.meta.env.VITE_ENTERPRISE_API as string | undefined)?.trim();
  if (fromEnv) return fromEnv.replace(/\/$/, "");
  if (typeof window !== "undefined" && window.location.port === "5173") {
    return "/enterprise-api";
  }
  return "http://127.0.0.1:8090";
}

export function readEnterpriseSettings(): {
  base: string;
  tenantId: string;
  token: string;
} {
  if (typeof window === "undefined") {
    return { base: defaultEnterpriseApiBase(), tenantId: "", token: "" };
  }
  return {
    base: window.localStorage.getItem(STORAGE.base) || defaultEnterpriseApiBase(),
    tenantId: window.localStorage.getItem(STORAGE.tenant) || "",
    token: window.localStorage.getItem(STORAGE.token) || "",
  };
}

export function writeEnterpriseSettings(next: {
  base: string;
  tenantId: string;
  token: string;
}): void {
  window.localStorage.setItem(STORAGE.base, next.base);
  window.localStorage.setItem(STORAGE.tenant, next.tenantId);
  window.localStorage.setItem(STORAGE.token, next.token);
}

export async function enterpriseFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const settings = readEnterpriseSettings();
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  if (settings.tenantId) headers.set("X-Tenant-Id", settings.tenantId);
  if (settings.token) headers.set("Authorization", `Bearer ${settings.token}`);
  const url = `${settings.base.replace(/\/$/, "")}${path}`;
  const response = await fetch(url, { ...init, headers });
  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!response.ok) {
    const detail =
      typeof body === "object" && body && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : `${response.status} ${path}`;
    throw new Error(detail);
  }
  return body as T;
}

export const FALLBACK_MANIFEST: EnterpriseManifest = {
  nav: [
    "Overview",
    "Organizations",
    "Populations",
    "Personas",
    "Experiments",
    "Tasks",
    "Environments",
    "Models",
    "Evaluations",
    "Analytics",
    "Governance",
    "Audit",
    "Infrastructure",
    "Settings",
  ].map((label) => ({
    id: label.toLowerCase(),
    label,
  })),
  wizard: [
    "Population",
    "Scenario",
    "Task",
    "Environment",
    "AI System",
    "Metrics",
    "Governance",
    "Scale",
    "Cost",
    "Launch",
  ].map((label) => ({
    id: label.toLowerCase().replace(/\s+/g, "_"),
    label,
  })),
  limitation: SYNTHETIC_DISCLAIMER,
  recommended_human_validation: true,
  synthetic_equivalent_to_human_research: false,
  default_policy: "SANDBOX_ONLY",
  stub_pages: ["tasks", "environments", "governance", "audit", "infrastructure"],
};
