import { describe, expect, it } from "vitest";

import { listCapabilities } from "@/features/freezone/capabilities/capabilityRegistry";

const sceneMasterCandidateCapability = listCapabilities().find(
  (capability) => capability.id === "scene_master_candidate",
)!;

describe("candidate capability branding", () => {
  it("shows the NuomiDrama production style while preserving its preset value", () => {
    const styleParam = sceneMasterCandidateCapability.params.find((param) => param.key === "style");

    expect(styleParam?.defaultValue).toBe("supertale_production");
    expect(styleParam?.options).toContainEqual({
      value: "supertale_production",
      label: "NuomiDrama 生产风格",
    });
    expect(styleParam?.options).not.toContainEqual(expect.objectContaining({ label: "SuperTale 生产风格" }));
  });

  it("uses neutral production wording in generated prompts", () => {
    const result = sceneMasterCandidateCapability.compose({
      inputUrls: [],
      params: { scene_id: "scene-1", style: "supertale_production" },
    });

    expect(result.prompt).toContain("Production-ready asset candidate.");
    expect(result.prompt).not.toContain("SuperTale");
  });
});
