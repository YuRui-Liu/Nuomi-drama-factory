import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { GroupVideoResult, isNonvisualVideoSkip } from "@/components/episode/narrative-workbench/group-video-result";

vi.mock("@/lib/queries/narrative-groups", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/queries/narrative-groups")>();
  return {
    ...actual,
    useNarrativeGroupVideoPrompts: vi.fn(() => ({
      data: { ok: true, data: { units: [] } }, isLoading: false, isError: false,
    })),
  };
});

describe("GroupVideoResult", () => {
  it("identifies only a completed nonvisual result without a video as skipped", () => {
    expect(isNonvisualVideoSkip({ status: "completed", revision: 6, actual_mode: "skipped_nonvisual" })).toBe(true);
    expect(isNonvisualVideoSkip({ status: "running", revision: 6, actual_mode: "skipped_nonvisual" })).toBe(false);
    expect(isNonvisualVideoSkip({ status: "completed", revision: 6, actual_mode: "i2va" })).toBe(false);
    expect(isNonvisualVideoSkip({
      status: "completed",
      revision: 6,
      actual_mode: "skipped_nonvisual",
      video_asset: "/media/group.mp4",
    })).toBe(false);
  });

  it("explains that a legacy nonvisual skip can now be retried", () => {
    render(<GroupVideoResult stage={{
      status: "completed",
      revision: 6,
      actual_mode: "skipped_nonvisual",
      manifest_asset: "/media/group.manifest.json",
    }} />);

    expect(screen.getByText("已跳过")).toBeInTheDocument();
    expect(screen.getByText("上次任务按旧策略跳过，未生成视频")).toBeInTheDocument();
    expect(screen.getByText("已有渲染首帧时，可直接点击上方“生成组合视频”重试")).toBeInTheDocument();
    expect(screen.queryByText("组合视频")).not.toBeInTheDocument();
    expect(screen.queryByText(/对白音轨/)).not.toBeInTheDocument();
    expect(screen.queryByText(/环境音轨/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "生成提示词" })).not.toBeInTheDocument();
    expect(screen.queryByText(/镜头切分/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /改用/ })).not.toBeInTheDocument();
    expect(document.querySelector("video")).not.toBeInTheDocument();
  });

  it("shows one physical video, stem state, and its logical VideoSpan dialogue sources", () => {
    render(<GroupVideoResult
      stage={{
        status: "completed", revision: 6, video_asset: "/media/group.mp4", manifest_asset: "/media/group.manifest.json",
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
    render(<GroupVideoResult stage={{ status: "completed", revision: 6, video_spans: [
      { beat_numbers: [1], start_seconds: 0, end_seconds: 5, dialogue_source: "external_tts" },
    ] }} onDialogueSourceChange={onDialogueSourceChange} />);
    screen.getByRole("button", { name: "改用 H3 原声" }).click();
    expect(onDialogueSourceChange).toHaveBeenCalledWith({ spanIndex: 0, dialogueSource: "h3_native" });
  });

  it("opens submitted prompt details for a completed manifest", async () => {
    const user = userEvent.setup();
    render(<GroupVideoResult
      project="project-1"
      episode={1}
      groupId="group-1"
      stage={{ status: "completed", revision: 6, manifest_asset: "/media/group.manifest.json" }}
    />);

    await user.click(screen.getByRole("button", { name: "生成提示词" }));
    expect(screen.getByRole("dialog", { name: "视频生成提示词" })).toBeInTheDocument();
  });
});
