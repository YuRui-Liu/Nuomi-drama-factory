import { describe, expect, it } from "vitest";

import { listCapabilities } from "@/features/freezone/capabilities/capabilityRegistry";

const sceneMasterCandidateCapability = listCapabilities().find(
  (capability) => capability.id === "scene_master_candidate",
)!;
const portraitFromRefCapability = listCapabilities().find(
  (capability) => capability.id === "portrait_from_ref",
)!;
const characterMultiViewCapability = listCapabilities().find(
  (capability) => capability.id === "character_multi_view_candidate",
)!;
const realSceneSketchRepairCapability = listCapabilities().find(
  (capability) => capability.id === "real_scene_sketch_repair",
)!;

const LEGACY_BRAND_PATTERN = /super[ _-]?tale/i;
const PRODUCTION_STYLE_DESCRIPTOR =
  "cohesive production style with consistent characters, environments, and shot-to-shot continuity";

describe("candidate capability branding", () => {
  it("shows the Nuomi Drama Factory production style while preserving its preset value", () => {
    const styleParam = sceneMasterCandidateCapability.params.find((param) => param.key === "style");

    expect(styleParam?.defaultValue).toBe("supertale_production");
    expect(styleParam?.options).toContainEqual({
      value: "supertale_production",
      label: "Nuomi Drama Factory 生产风格",
    });
    expect(styleParam?.options).not.toContainEqual(expect.objectContaining({ label: "SuperTale 生产风格" }));
  });

  it("maps the legacy preset value to a neutral descriptor in generated prompts", () => {
    const result = sceneMasterCandidateCapability.compose({
      inputUrls: [],
      params: { scene_id: "scene-1", style: "supertale_production" },
    });

    expect(result.prompt).toContain(PRODUCTION_STYLE_DESCRIPTOR);
    expect(result.prompt).toContain("Production-ready asset candidate.");
    expect(result.prompt).not.toMatch(LEGACY_BRAND_PATTERN);
  });

  it("preserves custom style descriptions in generated prompts", () => {
    const result = sceneMasterCandidateCapability.compose({
      inputUrls: [],
      params: { scene_id: "scene-1", style: "hand-painted watercolor" },
    });

    expect(result.prompt).toContain("Style: hand-painted watercolor.");
  });

  it.each([
    ["portrait identity", portraitFromRefCapability, { character: "Lin" }],
    ["character multi-view", characterMultiViewCapability, { character: "Lin" }],
    ["real-scene sketch repair", realSceneSketchRepairCapability, {}],
  ])("keeps %s model prompts free of the legacy brand", (_name, capability, params) => {
    const result = capability.compose({ inputUrls: [], params });

    expect(result.prompt).not.toMatch(LEGACY_BRAND_PATTERN);
  });
});
