import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GroupVideoPromptDrawer } from "@/components/episode/narrative-workbench/group-video-prompt-drawer";
import {
  narrativeGroupVideoPromptUnitKey,
  useNarrativeGroupVideoPrompts,
  type NarrativeGroupVideoPromptUnit,
} from "@/lib/queries/narrative-groups";
import { useRecordObservedBoundary } from "@/lib/queries/shot-continuity";

vi.mock("@/lib/queries/narrative-groups", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/queries/narrative-groups")>();
  return { ...actual, useNarrativeGroupVideoPrompts: vi.fn() };
});
vi.mock("@/lib/queries/shot-continuity", () => ({
  useRecordObservedBoundary: vi.fn(),
}));

const mockedQuery = vi.mocked(useNarrativeGroupVideoPrompts);
const mockedRecordObservedBoundary = vi.mocked(useRecordObservedBoundary);
const mutateAsync = vi.fn();

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
    mutateAsync.mockReset();
    mutateAsync.mockResolvedValue({ ok: true, data: { units: [] } });
    mockedRecordObservedBoundary.mockReturnValue({
      mutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useRecordObservedBoundary>);
    mockedQuery.mockReturnValue({
      data: { ok: true, data: { units: [{
        beat_ids: ["8", "9"], label: "Beat 8 → Beat 9", mode: "fl2va", duration_seconds: 10,
        input_summary: { first_frame: "Beat 8", last_frame: "Beat 9" },
        director_plan: { shots: [{ action: "turns quickly" }] },
        final_prompt: "[Shot 1] The hero turns quickly.",
        prompt_profile: { id: "minimax-h3-v1" }, quality_report: { passed: true },
        provider_task_id: "rh-123",
      }] } },
      isLoading: false, isError: false,
    } as unknown as ReturnType<typeof useNarrativeGroupVideoPrompts>);
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
    expect(screen.getByRole("status")).toHaveTextContent("提示词已复制");
    expect(screen.getByRole("link", { name: "下载 manifest" })).toBeInTheDocument();
  });

  it("reports an accessible error when Clipboard API is unavailable", async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
    render(<GroupVideoPromptDrawer open onOpenChange={vi.fn()} project="p" episode={1} groupId="g" />);
    await user.click(screen.getByRole("button", { name: "复制最终提示词" }));
    expect(screen.getByRole("alert")).toHaveTextContent("复制失败");
  });

  it("handles a rejected Clipboard promise without an unhandled rejection", async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    });
    render(<GroupVideoPromptDrawer open onOpenChange={vi.fn()} project="p" episode={1} groupId="g" />);
    await user.click(screen.getByRole("button", { name: "复制最终提示词" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("复制失败");
  });

  it("keeps legacy final prompts visible without a director plan", () => {
    mockedQuery.mockReturnValue({
      data: { ok: true, data: { units: [{ beat_ids: ["2"], mode: "i2va", duration_seconds: 5, director_plan: null, final_prompt: "legacy prompt" }] } },
      isLoading: false, isError: false,
    } as unknown as ReturnType<typeof useNarrativeGroupVideoPrompts>);
    render(<GroupVideoPromptDrawer open onOpenChange={vi.fn()} project="p" episode={1} groupId="g" />);
    expect(screen.getByText("旧版本无导演计划")).toBeInTheDocument();
    expect(screen.getByText("legacy prompt")).toBeInTheDocument();
  });

  it("shows S/I/M/C evidence, terminal contract boundaries, mode, and adapter", () => {
    mockedQuery.mockReturnValue({
      data: { ok: true, data: { units: [{
        segment_id: "segment-8",
        beat_ids: ["8", "9"], label: "Beat 8 → Beat 9", mode: "fl2va", duration_seconds: 10,
        final_prompt: "prompt",
        continuity_contracts: [
          { revision: 1, shot_id: "shot-8", boundary: { carry_in: "门关闭", planned_carry_out: "门半开" } },
          { revision: 2, shot_id: "shot-9", boundary: { carry_in: "门半开", planned_carry_out: "门完全打开" } },
        ],
        risk_report: {
          spatial: { dimension: "spatial", level: 2, reasons: ["越轴"] },
          identity: { dimension: "identity", level: 1, reasons: ["侧脸"] },
          motion: { dimension: "motion", level: 0, reasons: [] },
          continuity: { dimension: "continuity", level: 2, reasons: ["末态关键"] },
          blockers: ["缺少末帧"],
        },
        mode_decision: { requested: "auto", mode: "fl2va", reason_codes: ["reachable_exact_terminal"], blockers: [] },
        compiled_bundle: { adapter: "h3-ref", mode: "fl2va" },
        observed_carry_out: null,
      }] } },
      isLoading: false, isError: false,
    } as unknown as ReturnType<typeof useNarrativeGroupVideoPrompts>);

    render(<GroupVideoPromptDrawer open onOpenChange={vi.fn()} project="p" episode={1} groupId="g" />);

    expect(screen.getByText("空间风险 S2")).toBeInTheDocument();
    expect(screen.getByText("身份风险 I1")).toBeInTheDocument();
    expect(screen.getByText("运动风险 M0")).toBeInTheDocument();
    expect(screen.getByText("连续性风险 C2")).toBeInTheDocument();
    expect(screen.getByText(/越轴/)).toBeInTheDocument();
    expect(screen.getByText(/缺少末帧/)).toBeInTheDocument();
    expect(screen.getByText(/auto → fl2va/)).toBeInTheDocument();
    expect(screen.getByText(/h3-ref/)).toBeInTheDocument();
    expect(screen.getByText(/reachable_exact_terminal/)).toBeInTheDocument();
    expect(screen.getByText("门完全打开")).toBeInTheDocument();
    expect(screen.getByText("尚未验收实际末态")).toBeInTheDocument();
    expect(screen.getAllByText(/Revision [12]/)).toHaveLength(2);
  });

  it("explains legacy v1 units without continuity evidence", () => {
    render(<GroupVideoPromptDrawer open onOpenChange={vi.fn()} project="p" episode={1} groupId="g" />);
    expect(screen.getByText("该历史任务未记录连续性证据")).toBeInTheDocument();
  });

  it("records the observed terminal boundary with deviations and lock violations", async () => {
    const user = userEvent.setup();
    mockedQuery.mockReturnValue({
      data: { ok: true, data: { units: [{
        segment_id: "segment-8", beat_ids: ["8", "9"], label: "Beat 8 → Beat 9",
        mode: "fl2va", duration_seconds: 10, final_prompt: "prompt",
        continuity_contracts: [
          { revision: 7, shot_id: "shot-1", boundary: { carry_in: "门关闭", planned_carry_out: "第一镜计划末态" } },
          { revision: 1, shot_id: "shot-2", boundary: { carry_in: "门半开", planned_carry_out: "第二镜计划末态" } },
        ],
        risk_report: {
          spatial: { dimension: "spatial", level: 0, reasons: [] },
          identity: { dimension: "identity", level: 0, reasons: [] },
          motion: { dimension: "motion", level: 0, reasons: [] },
          continuity: { dimension: "continuity", level: 1, reasons: [] }, blockers: [],
        },
        mode_decision: { requested: "auto", mode: "fl2va", reason_codes: [], blockers: [] },
        compiled_bundle: { adapter: "base-h3", mode: "fl2va" }, observed_carry_out: null,
      }] } }, isLoading: false, isError: false,
    } as unknown as ReturnType<typeof useNarrativeGroupVideoPrompts>);
    render(<GroupVideoPromptDrawer open onOpenChange={vi.fn()} project="project-1" episode={3} groupId="group-1" />);

    expect(screen.getByText("第二镜计划末态")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Revision 7 · shot-1",
      "Revision 1 · shot-2",
    ]);
    await user.type(screen.getByLabelText("记录实际末态"), "门只打开一半");
    expect(screen.getByLabelText("偏差原因")).toBeRequired();
    expect(screen.getByRole("button", { name: "保存实际末态" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "接受该偏差" }));
    await user.type(screen.getByLabelText("偏差原因"), "成片动作幅度不足，接受后续承接");
    await user.click(screen.getByRole("checkbox", { name: "身份锁违规" }));
    await user.click(screen.getByRole("checkbox", { name: "镜头锁违规" }));
    await user.click(screen.getByRole("button", { name: "保存实际末态" }));

    expect(mockedRecordObservedBoundary).toHaveBeenCalledWith("project-1", 3, "group-1");
    expect(mutateAsync).toHaveBeenCalledWith({
      segmentId: "segment-8",
      contractRevision: 1,
      observedCarryOut: "门只打开一半",
      acceptDeviation: true,
      deviationReason: "成片动作幅度不足，接受后续承接",
      lockViolations: ["identity", "camera"],
    });
  });

  it("disables postflight controls while a save is pending", () => {
    mockedRecordObservedBoundary.mockReturnValue({
      mutateAsync,
      isPending: true,
    } as unknown as ReturnType<typeof useRecordObservedBoundary>);
    mockedQuery.mockReturnValue({
      data: { ok: true, data: { units: [{
        segment_id: "segment-8", beat_ids: ["8"], mode: "i2va", duration_seconds: 5,
        final_prompt: "prompt", continuity_contracts: [{ revision: 3, shot_id: "shot-8", boundary: {
          carry_in: "坐下", planned_carry_out: "站起",
        } }],
      }] } }, isLoading: false, isError: false,
    } as unknown as ReturnType<typeof useNarrativeGroupVideoPrompts>);
    render(<GroupVideoPromptDrawer open onOpenChange={vi.fn()} project="p" episode={1} groupId="g" />);
    expect(screen.getByRole("button", { name: "保存中…" })).toBeDisabled();
    expect(screen.getByLabelText("记录实际末态")).toBeDisabled();
  });
});
