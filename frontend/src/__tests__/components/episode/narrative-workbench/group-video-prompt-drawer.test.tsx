import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GroupVideoPromptDrawer } from "@/components/episode/narrative-workbench/group-video-prompt-drawer";
import {
  narrativeGroupVideoPromptUnitKey,
  useNarrativeGroupVideoPrompts,
  type NarrativeGroupVideoPromptUnit,
} from "@/lib/queries/narrative-groups";

vi.mock("@/lib/queries/narrative-groups", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/queries/narrative-groups")>();
  return { ...actual, useNarrativeGroupVideoPrompts: vi.fn() };
});

const mockedQuery = vi.mocked(useNarrativeGroupVideoPrompts);

describe("GroupVideoPromptDrawer", () => {
  it("accepts the Task 8 response without id and derives a safe stable key", () => {
    const unit = {
      beat_ids: ["beat-1", "beat-2"], label: "Beat 1 → Beat 2", mode: "fl2va",
      duration_seconds: 8.5, first_frame_url: "/api/v1/projects/demo/media/frames/beat-1.png",
      last_frame_url: "/api/v1/projects/demo/media/frames/beat-2.png", director_plan: null,
      final_prompt: "the exact submitted prompt", prompt_profile: null, quality_report: null,
      input_summary: { beat_ids: ["beat-1", "beat-2"] }, workflow: "workflow-136",
      model: "runninghub:minimax-h3", provider: "runninghub", provider_task_id: "task-42",
    } satisfies NarrativeGroupVideoPromptUnit;

    expect(narrativeGroupVideoPromptUnitKey(unit, 3)).toBe(
      "beat-1--beat-2::Beat 1 → Beat 2::task-42::3",
    );
  });

  beforeEach(() => {
    mockedQuery.mockReturnValue({
      data: { data: { units: [{
        beat_ids: ["8", "9"], label: "Beat 8 → Beat 9", mode: "fl2va", duration_seconds: 10,
        input_summary: { first_frame: "Beat 8", last_frame: "Beat 9" },
        director_plan: { shots: [{ action: "turns quickly" }] },
        final_prompt: "[Shot 1] The hero turns quickly.",
        prompt_profile: { id: "minimax-h3-v1" }, quality_report: { passed: true },
        provider_task_id: "rh-123",
      }] } },
      isLoading: false, isError: false,
    } as ReturnType<typeof useNarrativeGroupVideoPrompts>);
  });

  it("loads only while open and shows every review layer", () => {
    const { rerender } = render(<GroupVideoPromptDrawer open={false} onOpenChange={vi.fn()} project="p" episode={1} groupId="g" />);
    expect(mockedQuery).toHaveBeenLastCalledWith("p", 1, "g", false);

    rerender(<GroupVideoPromptDrawer open onOpenChange={vi.fn()} project="p" episode={1} groupId="g" />);
    expect(mockedQuery).toHaveBeenLastCalledWith("p", 1, "g", true);
    expect(screen.getByText("Beat 8 → Beat 9")).toBeInTheDocument();
    expect(screen.getByText("原始 Beat 信息 / 输入摘要")).toBeInTheDocument();
    expect(screen.getByText("导演计划")).toBeInTheDocument();
    expect(screen.getByText("最终提交提示词")).toBeInTheDocument();
    expect(screen.getByText("质量报告")).toBeInTheDocument();
  });

  it("copies the exact final prompt and exposes a client JSON download", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<GroupVideoPromptDrawer open onOpenChange={vi.fn()} project="p" episode={1} groupId="g" />);

    await user.click(screen.getByRole("button", { name: "复制最终提示词" }));
    expect(writeText).toHaveBeenCalledWith("[Shot 1] The hero turns quickly.");
    expect(screen.getByRole("button", { name: "下载 manifest" })).toBeInTheDocument();
  });

  it("keeps legacy final prompts visible without a director plan", () => {
    mockedQuery.mockReturnValue({
      data: { data: { units: [{ beat_ids: ["2"], mode: "i2va", duration_seconds: 5, director_plan: null, final_prompt: "legacy prompt" }] } },
      isLoading: false, isError: false,
    } as ReturnType<typeof useNarrativeGroupVideoPrompts>);
    render(<GroupVideoPromptDrawer open onOpenChange={vi.fn()} project="p" episode={1} groupId="g" />);
    expect(screen.getByText("旧版本无导演计划")).toBeInTheDocument();
    expect(screen.getByText("legacy prompt")).toBeInTheDocument();
  });
});
