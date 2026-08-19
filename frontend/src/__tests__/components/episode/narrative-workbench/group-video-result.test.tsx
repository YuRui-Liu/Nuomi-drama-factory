import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GroupVideoResult } from "@/components/episode/narrative-workbench/group-video-result";

describe("GroupVideoResult", () => {
  it("shows one physical video, stem state, and its logical VideoSpan dialogue sources", () => {
    render(<GroupVideoResult
      stage={{
        status: "completed", video_asset: "/media/group.mp4", manifest_asset: "/media/group.manifest.json",
        dialogue_stem_status: "succeeded", ambience_stem_status: "succeeded",
        video_spans: [
          { beat_numbers: [1, 2], start_seconds: 0, end_seconds: 8, dialogue_source: "external_tts" },
          { beat_numbers: [3], start_seconds: 8, end_seconds: 12, dialogue_source: "h3_native" },
        ],
      }}
      onDialogueSourceChange={vi.fn()}
    />);
    expect(screen.getByText("组合视频")).toBeInTheDocument();
    expect(screen.getByText(/对白音轨：已就绪/)).toBeInTheDocument();
    expect(screen.getByText(/镜头 1、2/)).toBeInTheDocument();
    expect(screen.getAllByText(/外部配音/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/H3 原声/).length).toBeGreaterThan(0);
  });

  it("leaves source switching as recomposition-only contract", () => {
    const onDialogueSourceChange = vi.fn();
    render(<GroupVideoResult stage={{ status: "completed", video_spans: [
      { beat_numbers: [1], start_seconds: 0, end_seconds: 5, dialogue_source: "external_tts" },
    ] }} onDialogueSourceChange={onDialogueSourceChange} />);
    screen.getByRole("button", { name: "改用 H3 原声" }).click();
    expect(onDialogueSourceChange).toHaveBeenCalledWith({ spanIndex: 0, dialogueSource: "h3_native" });
  });
});
