// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { processVideoUpload } from "@/features/canvas/nodes/VideoNode";

function read(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), relativePath), "utf8");
}

describe("VideoNode error notification contract", () => {
  it("uses preserved diagnostics for policy detection and dialog copy", () => {
    const source = read("src/features/canvas/nodes/VideoNode.tsx");

    expect(source).toContain(
      'const haystack = `${displayErrorMessage}\\n${diagnostics.details ?? ""}`',
    );
    expect(source).toContain("diagnostics.details ?? undefined");
    expect(source).not.toContain(
      'const haystack = `${displayErrorMessage}\\n${resolved.details ?? ""}`',
    );
  });

  it.each(["chunk loading", "transcode preparation"])(
    "uploads the original file and clears uploading when %s fails",
    async (failureMode) => {
      const file = new File(["video"], "source.mov", { type: "video/quicktime" });
      const uploadVideo = vi.fn(async () => ({
        url: "https://cdn.example/source.mov",
        filename: "source.mov",
        size: file.size,
      }));
      const updateNodeData = vi.fn();
      const clearTransientPreview = vi.fn();
      const loadTranscoder =
        failureMode === "chunk loading"
          ? vi.fn().mockRejectedValue(new Error("chunk load failed"))
          : vi.fn(async () => ({
              ensureWebSafeVideo: vi.fn().mockRejectedValue(new Error("probe failed")),
            }));
      const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);

      await processVideoUpload({
        file,
        projectId: "project-1",
        nodeId: "video-1",
        loadTranscoder,
        uploadVideo,
        updateNodeData,
        onTranscoded: vi.fn(),
        clearTransientPreview,
      });
      errorSpy.mockRestore();

      expect(uploadVideo).toHaveBeenCalledWith("project-1", file, file.name);
      expect(updateNodeData).toHaveBeenLastCalledWith("video-1", {
        videoUrl: "https://cdn.example/source.mov",
        previewImageUrl: null,
        sourceFileName: file.name,
        isUploading: false,
      });
      expect(clearTransientPreview).not.toHaveBeenCalled();
    },
  );
});
