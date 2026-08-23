// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";
import { canvasNodeDefinitions } from "@/features/canvas/domain/nodeRegistry";
import { hasImageGenPromptOverride } from "@/features/canvas/nodes/imageGenPrompt";

function read(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), relativePath), "utf8");
}

describe("image generation prompt helpers", () => {
  it("treats blank prompt text as no manual override", () => {
    expect(hasImageGenPromptOverride("")).toBe(false);
    expect(hasImageGenPromptOverride("   \n\t")).toBe(false);
    expect(hasImageGenPromptOverride("补充一点暖光")).toBe(true);
  });

  it("defaults extension style selection to null", () => {
    const data = canvasNodeDefinitions[
      CANVAS_NODE_TYPES.imageGen
    ].createDefaultData() as Record<string, unknown>;

    expect(data.extensionStyleId).toBeNull();
  });

  it("composes the extension style only for the submitted payload", () => {
    const source = read("src/features/canvas/nodes/ImageGenNode.tsx");

    expect(source).toContain(
      "composeImagePrompt(effectivePrompt, extensionStyleId)",
    );
    expect(source).toContain("prompt: composedPrompt");
    expect(source).not.toContain(
      "updateNodeData(id, { prompt: composedPrompt })",
    );
  });

  it("treats persisted nodes without an extension style as unselected", () => {
    const source = read("src/features/canvas/nodes/ImageGenNode.tsx");

    expect(source).toMatch(
      /const extensionStyleId =\s+typeof data\.extensionStyleId === 'string'\s+\? data\.extensionStyleId\s+: null;/,
    );
  });
});
