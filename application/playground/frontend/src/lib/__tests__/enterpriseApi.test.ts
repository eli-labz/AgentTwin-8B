import { describe, expect, it } from "vitest";

import {
  FALLBACK_MANIFEST,
  SYNTHETIC_DISCLAIMER,
} from "../enterpriseApi";

describe("enterprise console contract", () => {
  it("lists the Phase 7 primary nav and wizard steps", () => {
    expect(FALLBACK_MANIFEST.nav.map((item) => item.label)).toEqual([
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
    ]);
    expect(FALLBACK_MANIFEST.wizard.map((item) => item.label)).toEqual([
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
    ]);
  });

  it("never presents synthetic outputs as human research", () => {
    expect(FALLBACK_MANIFEST.synthetic_equivalent_to_human_research).toBe(false);
    expect(FALLBACK_MANIFEST.recommended_human_validation).toBe(true);
    expect(FALLBACK_MANIFEST.limitation).toBe(SYNTHETIC_DISCLAIMER);
    expect(SYNTHETIC_DISCLAIMER.toLowerCase()).toContain("not equivalent to human research");
  });
});
