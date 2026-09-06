import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GroupVideoStage } from "@/components/episode/narrative-workbench/group-video-stage";
import { groupFrameSummary } from "@/components/episode/narrative-workbench/group-video-stage";

const recommendedPlan = {
  revision: 3,
  source: "recommended" as const,
  units: [
    { id: "u-8-9", beat_ids: ["8", "9"] as [string, string], mode: "fl2va" as const, duration_seconds: 6, reason: "continuous" },
    { id: "u-10", beat_ids: ["10"] as [string], mode: "i2va" as const, duration_seconds: 4, reason: "ending" },
  ],
  total_duration_seconds: 10,
};

describe("GroupVideoStage", () => {
  it("shows inherited H3 and the actual automatic mode", () => {
    render(<GroupVideoStage modelId="runninghub:minimax-h3" mode="auto" hasFirstFrame hasLastFrame />);
    expect(screen.getByText(/MiniMax H3/)).toBeInTheDocument();
    expect(screen.getByText(/FL2V/)).toBeInTheDocument();
    expect(screen.getByText(/继承项目默认/)).toBeInTheDocument();
  });

  it("derives frame readiness and actual mode from every Beat, not render completion", () => {
    expect(groupFrameSummary([
      { beat_id: "1", has_first_frame: true, has_last_frame: true, actual_mode: "fl2va" },
      { beat_id: "2", has_first_frame: true, has_last_frame: false, actual_mode: "i2va" },
    ])).toEqual({ allHaveFirst: true, allHaveLast: false, modes: ["fl2va", "i2va"] });
  });

  it("keeps a temporary override local to the generation callback", () => {
    const generate = vi.fn();
    render(<GroupVideoStage modelId="runninghub:minimax-h3" mode="auto" hasFirstFrame hasLastFrame={false} onGenerate={generate} />);
    fireEvent.click(screen.getByRole("button", { name: "生成组合视频" }));
    expect(generate).toHaveBeenCalledWith({ video_model: "runninghub:minimax-h3", h3_mode: "auto" });
    expect(screen.getByText(/I2V/)).toBeInTheDocument();
  });

  it("hides reference controls for legacy models", () => {
    render(<GroupVideoStage modelId="runninghub:minimax-h3" mode="auto" hasFirstFrame hasLastFrame reference={{ required: false, count: 2, max: 5, valid: true }} />);
    expect(screen.queryByRole("button", { name: "管理参考图" })).not.toBeInTheDocument();
  });

  it("shows selected count and combines reference loading, error, invalid, and dirty generation gates", () => {
    const { rerender } = render(<GroupVideoStage modelId="runninghub:minimax-h3-ref" mode="auto" hasFirstFrame hasLastFrame onGenerate={vi.fn()} reference={{ required: true, count: 2, max: 5, valid: true, onManage: vi.fn() }} />);
    expect(screen.getByText("已选 2/5")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成组合视频" })).toBeEnabled();
    for (const reference of [
      { required: true, count: 2, max: 5, valid: true, loading: true },
      { required: true, count: 2, max: 5, valid: true, error: true },
      { required: true, count: 0, max: 5, valid: false },
      { required: true, count: 2, max: 5, valid: true, dirty: true },
    ]) {
      rerender(<GroupVideoStage modelId="runninghub:minimax-h3-ref" mode="auto" hasFirstFrame hasLastFrame onGenerate={vi.fn()} reference={reference} />);
      expect(screen.getByRole("button", { name: "生成组合视频" })).toBeDisabled();
    }
  });

  it("reports the single director task state and never offers tail-frame-only mode", () => {
    render(<GroupVideoStage modelId="runninghub:minimax-h3" mode="auto" hasFirstFrame hasLastFrame={false} taskStatus="running" />);
    expect(screen.getByText("生成中")).toBeInTheDocument();
    expect(screen.queryByText(/仅尾帧/)).not.toBeInTheDocument();
  });

  it("shows the recommended board by video unit with its total", () => {
    render(<GroupVideoStage modelId="runninghub:minimax-h3" mode="auto" hasFirstFrame hasLastFrame plan={recommendedPlan} />);
    expect(screen.getByText("Beat 8 → Beat 9 · 首尾帧 · 6秒")).toBeInTheDocument();
    expect(screen.getByText("Beat 10 · 首帧 · 4秒")).toBeInTheDocument();
    expect(screen.getByText("2 个视频单元 · 10秒 · 推荐方案")).toBeInTheDocument();
  });

  it("splits a pair into singleton units and saves only Beat groups", () => {
    const save = vi.fn();
    render(<GroupVideoStage modelId="runninghub:minimax-h3" mode="auto" hasFirstFrame hasLastFrame plan={recommendedPlan} onPlanSave={save} />);
    fireEvent.click(screen.getByRole("button", { name: "拆分 Beat 8 与 Beat 9" }));
    fireEvent.click(screen.getByRole("button", { name: "保存视频方案" }));
    expect(save).toHaveBeenCalledWith([
      { beatIds: ["8"] },
      { beatIds: ["9"] },
      { beatIds: ["10"] },
    ]);
  });

  it("keeps the server total and defers per-Beat durations after splitting", () => {
    render(<GroupVideoStage modelId="runninghub:minimax-h3" mode="auto" hasFirstFrame hasLastFrame plan={recommendedPlan} onPlanSave={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "拆分 Beat 8 与 Beat 9" }));

    expect(screen.getByText("3 个视频单元 · 10秒 · 已手调")).toBeInTheDocument();
    expect(screen.getAllByText(/保存后重算/)).toHaveLength(2);
    expect(screen.queryByText("Beat 8 · 首帧 · 3秒")).not.toBeInTheDocument();
    expect(screen.queryByText("Beat 9 · 首帧 · 3秒")).not.toBeInTheDocument();
  });

  it("requires saving a changed plan before generating", () => {
    const generate = vi.fn();
    render(<GroupVideoStage modelId="runninghub:minimax-h3" mode="auto" hasFirstFrame hasLastFrame plan={recommendedPlan} onPlanSave={vi.fn()} onGenerate={generate} />);
    fireEvent.click(screen.getByRole("button", { name: "拆分 Beat 8 与 Beat 9" }));

    expect(screen.getByRole("button", { name: "生成组合视频" })).toBeDisabled();
    expect(screen.getByText("请先保存视频方案")).toBeInTheDocument();
  });

  it.each([
    { taskStatus: "queued" as const, planSaving: false },
    { taskStatus: "running" as const, planSaving: false },
    { taskStatus: "pending" as const, planSaving: true },
  ])("disables generation while $taskStatus or saving=$planSaving", ({ taskStatus, planSaving }) => {
    render(<GroupVideoStage modelId="runninghub:minimax-h3" mode="auto" hasFirstFrame hasLastFrame plan={recommendedPlan} taskStatus={taskStatus} planSaving={planSaving} onGenerate={vi.fn()} />);
    expect(screen.getByRole("button", { name: "生成组合视频" })).toBeDisabled();
  });

  it("merges adjacent singleton units without overlapping another pair", () => {
    const save = vi.fn();
    const singletonPlan = {
      ...recommendedPlan,
      units: [
        { id: "u-8", beat_ids: ["8"] as [string], mode: "i2va" as const, duration_seconds: 3, reason: "start" },
        { id: "u-9", beat_ids: ["9"] as [string], mode: "i2va" as const, duration_seconds: 3, reason: "middle" },
        { id: "u-10", beat_ids: ["10"] as [string], mode: "i2va" as const, duration_seconds: 4, reason: "ending" },
      ],
    };
    render(<GroupVideoStage modelId="runninghub:minimax-h3" mode="auto" hasFirstFrame hasLastFrame plan={singletonPlan} onPlanSave={save} />);
    fireEvent.click(screen.getByRole("button", { name: "合并 Beat 8 与 Beat 9" }));
    fireEvent.click(screen.getByRole("button", { name: "保存视频方案" }));
    expect(save).toHaveBeenCalledWith([
      { beatIds: ["8", "9"] },
      { beatIds: ["10"] },
    ]);
  });

  it("lets the user re-pair any adjacent Beat boundary in one action", () => {
    const save = vi.fn();
    const staggeredPlan = {
      ...recommendedPlan,
      units: [
        { id: "u-20-21", beat_ids: ["20", "21"] as [string, string], mode: "fl2va" as const, duration_seconds: 10, reason: "continuous" },
        { id: "u-22", beat_ids: ["22"] as [string], mode: "i2va" as const, duration_seconds: 5, reason: "cut" },
        { id: "u-23-24", beat_ids: ["23", "24"] as [string, string], mode: "fl2va" as const, duration_seconds: 10, reason: "continuous" },
      ],
      total_duration_seconds: 25,
    };

    render(<GroupVideoStage modelId="runninghub:minimax-h3" mode="auto" hasFirstFrame hasLastFrame plan={staggeredPlan} onPlanSave={save} />);

    expect(screen.getByText("手动调整相邻 Beat")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "合并 Beat 21 与 Beat 22" }));
    fireEvent.click(screen.getByRole("button", { name: "保存视频方案" }));

    expect(save).toHaveBeenCalledWith([
      { beatIds: ["20"] },
      { beatIds: ["21", "22"] },
      { beatIds: ["23", "24"] },
    ]);
  });

  it.each([
    { taskStatus: "queued" as const, planSaving: false },
    { taskStatus: "running" as const, planSaving: false },
    { taskStatus: "pending" as const, planSaving: true },
  ])("disables plan editing while $taskStatus or saving=$planSaving", ({ taskStatus, planSaving }) => {
    render(<GroupVideoStage modelId="runninghub:minimax-h3" mode="auto" hasFirstFrame hasLastFrame plan={recommendedPlan} taskStatus={taskStatus} planSaving={planSaving} onPlanSave={vi.fn()} />);
    expect(screen.getByRole("button", { name: "拆分 Beat 8 与 Beat 9" })).toBeDisabled();
  });
});
