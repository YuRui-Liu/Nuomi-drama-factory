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
      /const submitDisabled\s*=\s*isNarrativeGroupGrid\s*\|\|\s*isGenerating\s*\|\|\s*isAssetDesignRunBusy\s*\|\|\s*assetDesignActionBusy\s*\|\|\s*!selectedModel\s*\|\|\s*!hasEffectivePrompt/,
    );
  });

  it("routes bound asset designs through explicit single-run orchestration and exposes their source and status", () => {
    const source = readFileSync(
      "src/features/canvas/nodes/ImageGenNode.tsx",
      "utf8",
    );

    expect(source).toMatch(/if \(isAssetDesignBound\)\s*\{[\s\S]*?await submitBoundAssetDesign\(\)/);
    expect(source).toContain("!canAutoCommitOnGenerate && !isAssetDesignBound");
    expect(source).toContain("canAutoCommitOnGenerate || isAssetDesignBound ? 1 : count");
    expect(source).toContain('aria-label="资产设计状态"');
    expect(source).toContain("设计来源：");
    expect(source).toContain("assetRunLabel(assetDesignRun.status)");
    expect(source).toContain("performAssetDesignAction('cancel')");
    expect(source).toContain("performAssetDesignAction('retry')");
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
