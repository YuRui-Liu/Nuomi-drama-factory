// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";

import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";
import { canvasNodeDefinitions } from "@/features/canvas/domain/nodeRegistry";
import { composeImagePrompt } from "@/features/canvas/extension-styles/composePrompt";
import {
  buildImageGenerationRequestPayloads,
  submitImageGenerationPayloadAtIndex,
} from "@/features/canvas/nodes/ImageGenNode";
import { hasImageGenPromptOverride } from "@/features/canvas/nodes/imageGenPrompt";

function read(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), relativePath), "utf8");
}

const STYLE_ID = "drama_ext.japanese_cel_animation";

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

  it("applies the selected extension style to every concurrent request", () => {
    const basePayload = {
      prompt: "女孩站在雨中的车站。\n不要改变这行。",
      model: "gpt-image-2",
    };

    const payloads = buildImageGenerationRequestPayloads(
      basePayload,
      STYLE_ID,
      3,
    );
    const expectedPrompt = composeImagePrompt(
      basePayload.prompt,
      STYLE_ID,
    );

    expect(payloads).toHaveLength(3);
    expect(payloads.map((payload) => payload.prompt)).toEqual([
      expectedPrompt,
      expectedPrompt,
      expectedPrompt,
    ]);
  });

  it.each([null, undefined, "drama_ext.unknown"])(
    "keeps the baseline prompt byte-for-byte for extension id %s",
    (extensionStyleId) => {
      const prompt = "  原始提示词。\n第二行保持不变。  ";

      const [payload] = buildImageGenerationRequestPayloads(
        { prompt, model: "baseline" },
        extensionStyleId,
        1,
      );

      expect(payload.prompt).toBe(prompt);
    },
  );

  it("does not mutate the persisted prompt while building requests", () => {
    const nodeData = {
      prompt: "用户保存的原始提示词",
      extensionStyleId: STYLE_ID,
    };
    const basePayload = { prompt: nodeData.prompt, model: "gpt-image-2" };

    const [payload] = buildImageGenerationRequestPayloads(
      basePayload,
      nodeData.extensionStyleId,
      1,
    );

    expect(payload.prompt).not.toBe(nodeData.prompt);
    expect(nodeData.prompt).toBe("用户保存的原始提示词");
    expect(basePayload.prompt).toBe("用户保存的原始提示词");
  });

  it("submits every composed payload at its matching concurrent index", async () => {
    const payloads = buildImageGenerationRequestPayloads(
      { prompt: "雨夜车站", model: "gpt-image-2" },
      STYLE_ID,
      3,
    );
    const submitter = vi.fn().mockImplementation(
      async (_projectId: string, payload: Record<string, unknown>) => payload,
    );

    await Promise.all(
      payloads.map((_, runIndex) => submitImageGenerationPayloadAtIndex(
        "project-1",
        payloads,
        runIndex,
        { canvasId: "canvas-1", nodeId: "node-1" },
        submitter,
      )),
    );

    expect(submitter).toHaveBeenCalledTimes(3);
    for (const [runIndex, payload] of payloads.entries()) {
      expect(submitter).toHaveBeenNthCalledWith(runIndex + 1, "project-1", {
        ...payload,
        canvasId: "canvas-1",
        nodeId: "node-1",
      });
    }
  });

  it.each([null, "drama_ext.unknown"])(
    "submits the byte-identical baseline prompt for extension id %s",
    async (extensionStyleId) => {
      const prompt = "  原提示词\n保持空白  ";
      const payloads = buildImageGenerationRequestPayloads(
        { prompt, model: "baseline" },
        extensionStyleId,
        1,
      );
      const submitter = vi.fn().mockResolvedValue({ task_key: "task-1" });

      await submitImageGenerationPayloadAtIndex(
        "project-1",
        payloads,
        0,
        { canvasId: "canvas-1", nodeId: "node-1" },
        submitter,
      );

      expect(submitter).toHaveBeenCalledOnce();
      expect(submitter.mock.calls[0]?.[1]?.prompt).toBe(prompt);
    },
  );

  it("composes the extension style only for the submitted payload", () => {
    const source = read("src/features/canvas/nodes/ImageGenNode.tsx");

    expect(source).toMatch(
      /buildImageGenerationRequestPayloads\(\s+baseGenPayload,\s+extensionStyleId,\s+total,\s+\)/,
    );
    expect(source).toMatch(
      /submitImageGenerationPayloadAtIndex\(\s+projectId,\s+genPayloads,\s+runIndex,\s+\{ canvasId, nodeId: id \},\s+submitFreezoneGen,\s+\)/,
    );
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
