import { describe, expect, it } from "vitest";

import {
  availableVideoModels,
  effectiveVideoMode,
  resolveVideoModel,
  videoModelRequest,
  type VideoModelCatalogItem,
} from "@/lib/queries/media-models";
import { queryKeys } from "@/lib/query-keys";

describe("video media model contract", () => {
  it("selects FL2VA for first and last frames and I2VA for first frame only", () => {
    expect(effectiveVideoMode("auto", true, true)).toBe("fl2va");
    expect(effectiveVideoMode("auto", true, false)).toBe("i2va");
    expect(effectiveVideoMode("fl2va", true, false)).toBe("fl2va");
  });

  it.each(["newapi_seedance-1.0-pro-fast", "unknown:video-model"])(
    "falls back from saved model %s to the only available workflow",
    (savedModel) => {
      const catalog: VideoModelCatalogItem[] = [
        { id: "runninghub:disabled", label: "Disabled", provider: "runninghub", available: false, supported_modes: ["auto"], default_mode: "auto" },
        { id: "runninghub:minimax-h3", label: "RunningHub MiniMax H3", provider: "runninghub", available: true, supported_modes: ["auto", "i2va", "fl2va"], default_mode: "auto" },
      ];

      expect(availableVideoModels(catalog).map((item) => item.id)).toEqual(["runninghub:minimax-h3"]);
      expect(resolveVideoModel(savedModel, catalog)?.id).toBe("runninghub:minimax-h3");
    },
  );

  it("never resolves an unavailable saved workflow", () => {
    const catalog: VideoModelCatalogItem[] = [
      { id: "runninghub:minimax-h3", label: "RunningHub MiniMax H3", provider: "runninghub", available: false, supported_modes: ["auto"], default_mode: "auto" },
      { id: "runninghub:future", label: "Future Workflow", provider: "runninghub", available: true, supported_modes: ["auto"], default_mode: "auto" },
    ];

    expect(resolveVideoModel("runninghub:minimax-h3", catalog)?.id).toBe("runninghub:future");
  });

  it("serializes only stable model identifiers and mode", () => {
    expect(videoModelRequest("runninghub:minimax-h3", "auto")).toEqual({
      video_model: "runninghub:minimax-h3",
      h3_mode: "auto",
    });
    expect(queryKeys.videoModels()).toEqual(["media-capabilities", "video", "models"]);
  });
});
