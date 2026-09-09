// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ProviderModelPicker } from "@/features/canvas/ui/ProviderModelPicker";

describe("ProviderModelPicker empty catalog", () => {
  it("does not expose a stale selected model as an executable choice", () => {
    render(
      <ProviderModelPicker
        selectedModelId="huimeng/gpt-image-2"
        models={[]}
        onChange={vi.fn()}
      />,
    );

    const trigger = screen.getByRole("button");
    expect(trigger).toBeDisabled();
    expect(trigger).not.toHaveTextContent("LingShan-G2");
    expect(trigger).not.toHaveTextContent("huimeng/gpt-image-2");
  });

  it("keeps unavailable hardcoded models out of image generation consumers", () => {
    const consumers = [
      "src/features/canvas/nodes/ImageGenNode.tsx",
      "src/features/canvas/ui/RedrawOverlay.tsx",
      "src/features/canvas/ui/OutpaintEditorOverlay.tsx",
      "src/features/canvas/ui/UpscaleEditorOverlay.tsx",
    ];

    for (const path of consumers) {
      expect(readFileSync(path, "utf8")).not.toContain("SHARED_MODELS.find");
    }

    const imageGenNode = readFileSync(consumers[0], "utf8");
    expect(imageGenNode).toMatch(
      /const submitDisabled\s*=\s*isGenerating\s*\|\|\s*!selectedModel\s*\|\|/,
    );
  });

  it.each([
    ["LightEditorPanel", "disabled={!selectedModel}"],
    ["MultiAngleEditorPanel", "disabled={!selectedModel}"],
    ["EraseOverlay", "disabled={submitting || !imageDims || !selectedModel}"],
    ["Scene360Overlay", "disabled={!selectedModel}"],
    ["GridActionConfirmOverlay", "disabled={!selectedModel}"],
  ])("disables %s submission when the live model catalog is empty", (name, disabledProp) => {
    const source = readFileSync(
      `src/features/canvas/ui/${name}.tsx`,
      "utf8",
    );

    expect(source).toContain("if (!selectedModel) return;");
    expect(source).toContain(disabledProp);
    expect(source).toContain("modelAvailabilityReason");
    expect(source).toContain("NODE_GENERATE_BUTTON_DISABLED_CLASS");
  });
});
