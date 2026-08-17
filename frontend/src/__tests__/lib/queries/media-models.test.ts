import { describe, expect, it } from "vitest";

import { effectiveVideoMode, mergeVideoModelCatalog, videoModelRequest } from "@/lib/queries/media-models";
import { queryKeys } from "@/lib/query-keys";

describe("video media model contract", () => {
  it("selects FL2VA for first and last frames and I2VA for first frame only", () => {
    expect(effectiveVideoMode("auto", true, true)).toBe("fl2va");
    expect(effectiveVideoMode("auto", true, false)).toBe("i2va");
    expect(effectiveVideoMode("fl2va", true, false)).toBe("fl2va");
  });

  it("keeps legacy backends alongside capability models", () => {
    const merged = mergeVideoModelCatalog(
      [{ id: "runninghub:minimax-h3", label: "MiniMax H3", provider: "runninghub", available: true, supported_modes: ["auto"], default_mode: "auto" }],
      [{ value: "huimeng_seedance-1.0-pro-fast", label: "Seedance", is_default: true, is_seedance2: false, dialogue_only: false }],
    );
    expect(merged.map((item) => item.id)).toEqual([
      "runninghub:minimax-h3", "huimeng_seedance-1.0-pro-fast",
    ]);
  });

  it("serializes only stable model identifiers and mode", () => {
    expect(videoModelRequest("runninghub:minimax-h3", "auto")).toEqual({
      video_model: "runninghub:minimax-h3",
      h3_mode: "auto",
    });
    expect(queryKeys.videoModels()).toEqual(["media-capabilities", "video", "models"]);
  });
});
