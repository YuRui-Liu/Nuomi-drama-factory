import { fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  review: vi.fn(),
  edit: vi.fn(),
  activate: vi.fn(),
  abandon: vi.fn(),
  migrate: vi.fn(),
  tasks: [] as import("@/types/task").Task[],
  reviewState: {} as { isSuccess?: boolean; isError?: boolean; variables?: string; error?: Error },
  errorState: {} as { isError?: boolean; error?: Error },
}));

vi.mock("@/lib/queries/tasks", () => ({ useTasks: () => ({ data: { ok: true, data: mocks.tasks } }) }));

vi.mock("@/lib/queries/director-plans", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/queries/director-plans")>();
  return {
    ...actual,
    useDirectorPlans: () => ({ data: { ok: true, data: revisions }, isLoading: false }),
    useDirectorPlanComparison: () => ({
      data: { ok: true, data: { base: revisions[0], candidate: revisions[1] } },
    }),
    useDirectorPlanMigration: () => ({ data: { ok: true, data: revisions[1].migration_report } }),
    useCreateDirectorPlan: () => ({ mutate: mocks.create, isPending: false }),
    useReviewDirectorPlan: () => ({ mutate: mocks.review, isPending: false, ...mocks.reviewState }),
    useEditDirectorPlan: () => ({ mutate: mocks.edit, isPending: false, ...mocks.errorState }),
    useActivateDirectorPlan: () => ({ mutate: mocks.activate, isPending: false, ...mocks.errorState }),
    useAbandonDirectorPlan: () => ({ mutate: mocks.abandon, isPending: false, ...mocks.errorState }),
    useUpdateDirectorPlanMigration: () => ({ mutate: mocks.migrate, isPending: false, ...mocks.errorState }),
  };
});

import type { DirectorPlanRevision } from "@/lib/queries/director-plans";
import { DirectorReviewWorkbench } from "@/components/episode/director-review/director-review-workbench";

it("shows a failed independent review after reopening the workbench", () => {
  mocks.tasks = [{ task_type: "director_plan", username: "local", project: "demo",
    episode: 1, scope: "creative-review:rev-2", status: "failed", progress: 0.05,
    error: "director_plan_failed" }];
  render(<DirectorReviewWorkbench project="demo" episode={1} onClose={() => {}} />);
  expect(screen.getByText(/独立 QC 复查失败：director_plan_failed/)).toBeInTheDocument();
  mocks.tasks = [];
});

function shot(id: string, action: string) {
  return {
    id,
    source_span_ids: [`span-${id}`],
    subject: "阿远",
    action,
    visible_start_state: "收音机没有亮",
    visible_end_state: "收音机亮起",
    shot_size: "medium",
    camera_angle: "eye_level",
    composition: "centered",
    camera_motion: "static",
    dialogue_source_ids: [],
    duration_seconds: 4,
  };
}

const base: DirectorPlanRevision = {
  revision_id: "rev-1",
  parent_revision_id: null,
  episode: 1,
  status: "active",
  source_script_hash: "hash-1",
  director_model: "deepseek-v4-flash",
  prompt_version: "director-v2",
  project_style_snapshot_id: "style-1",
  groups: [{
    id: "ng-01",
    ordinal: 1,
    source_span_ids: ["span-shot-1"],
    scene_anchor: "修理铺",
    time_anchor: "白天",
    objective: "修好收音机",
    visible_turn: "收音机亮起",
    relation_to_previous: "single",
    shots: [shot("shot-1", "转动旋钮")],
    style_snapshot_id: "style-1",
  }],
  validation_report: { passed: true, issues: [], version: 1 },
  migration_report: { items: [] },
  created_at: "2026-08-29T00:00:00Z",
  activated_at: "2026-08-29T00:01:00Z",
};

const candidate: DirectorPlanRevision = {
  ...base,
  revision_id: "rev-2",
  parent_revision_id: "rev-1",
  status: "review_required",
  groups: [
    { ...base.groups[0], shots: [shot("shot-1", "转动旋钮"), shot("shot-2", "侧耳倾听")] },
    {
      ...base.groups[0],
      id: "ng-02",
      ordinal: 2,
      objective: "确认信号",
      relation_to_previous: "progressive",
      shots: [shot("shot-3", "记录频率")],
    },
  ],
  migration_report: {
    items: [
      {
        item_id: "asset-high",
        old_asset_id: "asset-1",
        old_shot_id: "shot-old-1",
        new_shot_id: "shot-1",
        confidence: "high",
        score: 0.92,
        reuse_mode: "reuse",
        decision: "accepted",
        evidence: { source_overlap: 1, subject_overlap: 1, scene_match: 1, action_similarity: 0.8, shot_semantic_similarity: 0.8 },
      },
      {
        item_id: "asset-reference",
        old_asset_id: "asset-2",
        old_shot_id: "shot-old-2",
        new_shot_id: "shot-2",
        confidence: "medium",
        score: 0.7,
        reuse_mode: "reference_only",
        decision: "review",
        evidence: { source_overlap: 0.8, subject_overlap: 1, scene_match: 1, action_similarity: 0.4, shot_semantic_similarity: 0.5 },
      },
      {
        item_id: "asset-low",
        new_shot_id: "shot-3",
        confidence: "low",
        score: 0.25,
        reuse_mode: "reuse",
        decision: "review",
        evidence: { source_overlap: 0.2, subject_overlap: 0.2, scene_match: 0, action_similarity: 0.4, shot_semantic_similarity: 0.3 },
      },
    ],
  },
};

const revisions = [base, candidate];

describe("DirectorReviewWorkbench", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows actionable mutation failures without losing the selected version", () => {
    mocks.errorState = { isError: true, error: new Error("request failed") };
    try {
      render(<DirectorReviewWorkbench project="demo" episode={1} onClose={vi.fn()} />);
      expect(screen.getAllByRole("alert")).toHaveLength(4);
      expect(screen.getByRole("button", { name: "在镜头 shot-2 前拆分" })).toBeInTheDocument();
    } finally { mocks.errorState = {}; }
  });

  it("locates only assessment shot IDs that exist in the selected revision", async () => {
    candidate.creative_contract_version = 1;
    candidate.creative_feedback = { issues: [], author_rounds: [], qc_rounds: [{ round_id: "qc", dispositions: [], assessments: ["shot-2", "deleted-shot"].map((shot_id) => ({ shot_id, dimension: "framing", conclusion: "persists", reason: "检查证据", evidence_paths: [] })) }] };
    try {
      render(<DirectorReviewWorkbench project="demo" episode={1} onClose={vi.fn()} />);
      fireEvent.click(screen.getByRole("tab", { name: "审核问题 (0)" }));
      fireEvent.click(screen.getByText("最近一轮逐镜检查证据"));
      expect(screen.queryByRole("button", { name: "定位镜头 deleted-shot" })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "定位镜头 shot-2" }));
      await waitFor(() => expect(document.getElementById("director-shot-rev-2-shot-2")).toHaveFocus());
      expect(screen.getByRole("tab", { name: "镜头内容" })).toHaveAttribute("aria-selected", "true");
    } finally { delete candidate.creative_contract_version; delete candidate.creative_feedback; }
  });

  it("does not carry submitted QC state into another selected revision", () => {
    mocks.reviewState = { isSuccess: true, variables: "rev-2" };
    base.status = "review_required";
    base.creative_contract_version = 1;
    base.creative_review_status = "unreviewed";
    try {
      render(<DirectorReviewWorkbench project="demo" episode={1} onClose={vi.fn()} />);
      fireEvent.click(screen.getByRole("button", { name: /版本 1/ }));
      expect(screen.getByRole("button", { name: "提交独立 QC 复查" })).toBeEnabled();
      expect(screen.queryByText("独立 QC 已提交，正在获取任务状态。")).not.toBeInTheDocument();
    } finally {
      mocks.reviewState = {};
      base.status = "active";
      delete base.creative_contract_version;
      delete base.creative_review_status;
    }
  });

  it("allows QC retry when an observed completed task later disappears", () => {
    mocks.reviewState = { isSuccess: true, variables: "rev-2" };
    candidate.creative_contract_version = 1;
    candidate.creative_review_status = "needs_revision";
    try {
      const view = render(<DirectorReviewWorkbench project="demo" episode={1} onClose={vi.fn()} />);
      expect(screen.getByRole("button", { name: "提交独立 QC 复查" })).toBeDisabled();
      mocks.tasks = [{ task_type: "director_plan", username: "local", project: "demo", episode: 1, scope: "creative-review:rev-2", status: "running", progress: 0.2 }];
      view.rerender(<DirectorReviewWorkbench project="demo" episode={1} onClose={vi.fn()} />);
      mocks.tasks = [{ ...mocks.tasks[0], status: "completed", progress: 1 }];
      view.rerender(<DirectorReviewWorkbench project="demo" episode={1} onClose={vi.fn()} />);
      mocks.tasks = [];
      view.rerender(<DirectorReviewWorkbench project="demo" episode={1} onClose={vi.fn()} />);
      expect(screen.getByRole("button", { name: "提交独立 QC 复查" })).toBeEnabled();
      expect(screen.queryByText("独立 QC 已提交，正在获取任务状态。")).not.toBeInTheDocument();
    } finally {
      mocks.tasks = [];
      mocks.reviewState = {};
      delete candidate.creative_contract_version;
      delete candidate.creative_review_status;
    }
  });

  it("keeps discovery locked when the task query still contains an older terminal result", () => {
    mocks.reviewState = { isSuccess: true, variables: "rev-2" };
    mocks.tasks = [{ task_type: "director_plan", username: "local", project: "demo", episode: 1, scope: "creative-review:rev-2", status: "completed", progress: 1 }];
    candidate.creative_contract_version = 1;
    candidate.creative_review_status = "needs_revision";
    try {
      render(<DirectorReviewWorkbench project="demo" episode={1} onClose={vi.fn()} />);
      expect(screen.getByRole("button", { name: "提交独立 QC 复查" })).toBeDisabled();
    } finally {
      mocks.tasks = [];
      mocks.reviewState = {};
      delete candidate.creative_contract_version;
      delete candidate.creative_review_status;
    }
  });

  it("queues QC for the edited candidate without replanning", () => {
    candidate.creative_contract_version = 1;
    candidate.creative_review_status = "needs_revision";
    try {
      render(<DirectorReviewWorkbench project="demo" episode={1} onClose={vi.fn()} />);
      fireEvent.click(screen.getByRole("button", { name: "提交独立 QC 复查" }));
      expect(mocks.review).toHaveBeenCalledWith("rev-2");
      expect(mocks.create).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "激活此版本" })).toBeDisabled();
    } finally {
      delete candidate.creative_contract_version;
      delete candidate.creative_review_status;
    }
  });

  it("keeps action and sources intact while director details start folded", () => {
    const original = candidate.groups[0].shots[0].action;
    candidate.groups[0].shots[0].action = "转动旋钮\n侧耳倾听，保持原文动作。";
    candidate.groups[0].shots[0].dialogue_source_ids = ["dialogue-source-123"];
    try {
      render(<DirectorReviewWorkbench project="demo" episode={1} onClose={vi.fn()} />);
      const summary = screen.getByText("导演细节 · 镜头 shot-1");
      const details = summary.closest("details")!;
      expect(details).not.toHaveAttribute("open");
      expect(screen.getByText("转动旋钮 侧耳倾听，保持原文动作。").textContent).toBe("转动旋钮\n侧耳倾听，保持原文动作。");
      fireEvent.click(summary);
      expect(within(details).getByText("对白来源：dialogue-source-123")).toBeInTheDocument();
      expect(within(details).getByText("焦段与焦点")).toBeInTheDocument();
      expect(within(details).getByText("镜头衔接")).toBeInTheDocument();
    } finally {
      candidate.groups[0].shots[0].action = original;
      candidate.groups[0].shots[0].dialogue_source_ids = [];
    }
  });

  it("blocks duplicate QC and structural edits while persistent task runs", () => {
    mocks.tasks = [{ task_type: "director_plan", username: "local", project: "demo", episode: 1, scope: "creative-review:rev-2", status: "running", progress: 0.2 }];
    candidate.creative_contract_version = 1;
    candidate.creative_review_status = "unreviewed";
    try {
      render(<DirectorReviewWorkbench project="demo" episode={1} onClose={vi.fn()} />);
      expect(screen.getByRole("button", { name: "提交独立 QC 复查" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "在镜头 shot-2 前拆分" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "激活此版本" })).toBeDisabled();
      expect(screen.getByText(/独立 QC 复查进行中/)).toBeInTheDocument();
    } finally {
      mocks.tasks = [];
      delete candidate.creative_contract_version;
      delete candidate.creative_review_status;
    }
  });

  it("keeps group commands in folded structure controls and dispatches original IDs", () => {
    render(<DirectorReviewWorkbench project="demo" episode={1} onClose={vi.fn()} />);
    const menu = screen.getAllByText("调整结构")[0].closest("details")!;
    expect(menu).not.toHaveAttribute("open");
    fireEvent.click(within(menu).getByRole("button", { name: "合并 ng-01 与 ng-02" }));
    expect(mocks.edit).toHaveBeenCalledWith({ revisionId: "rev-2", command: { kind: "merge_adjacent_groups", left_group_id: "ng-01", right_group_id: "ng-02" } });
  });

  it("shows revision history, real stages and old/new narrative structures", () => {
    render(<DirectorReviewWorkbench project="demo" episode={1} onClose={vi.fn()} />);

    expect(screen.getByRole("button", { name: "重新导演分镜" })).toBeInTheDocument();
    expect(screen.getByText("当前生效")).toBeInTheDocument();
    expect(screen.getAllByText("待人工审核")).not.toHaveLength(0);
    expect(screen.getByRole("tab", { name: "镜头内容" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByText("旧版 · rev-1")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "版本变化" }));
    expect(screen.getByText("旧版 · rev-1")).toBeInTheDocument();
    expect(screen.getByText("新版 · rev-2")).toBeInTheDocument();
    expect(screen.getByText("结构规划完成，等待资产迁移确认")).toBeInTheDocument();
  });

  it("dispatches one immutable edit command and exposes migration confidence semantics", () => {
    render(<DirectorReviewWorkbench project="demo" episode={1} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "在镜头 shot-2 前拆分" }));
    expect(mocks.edit).toHaveBeenCalledWith({
      revisionId: "rev-2",
      command: { kind: "split_group", group_id: "ng-01", before_shot_id: "shot-2" },
    });

    fireEvent.click(screen.getByRole("tab", { name: "素材复用" }));

    expect(screen.getByText("高置信度")).toBeInTheDocument();
    expect(screen.getByText("中置信度")).toBeInTheDocument();
    expect(screen.getByText("低置信度")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "仅作参考 asset-low" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "接受 asset-low" })).not.toBeInTheDocument();
    expect(screen.getByText("匹配依据不足，暂不支持迁移。原素材仍保留，可在生成时手动添加为临时参考。" )).toBeInTheDocument();
    expect(screen.getByText("仅作视觉参考，不会覆盖正式素材")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "仅作参考 asset-reference" }));
    expect(mocks.migrate).toHaveBeenCalledWith({
      revisionId: "rev-2",
      itemId: "asset-reference",
      decision: "reference_only",
    });
  });

  it("activates, abandons, restores and starts a new directing task", () => {
    render(<DirectorReviewWorkbench project="demo" episode={1} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "激活此版本" }));
    fireEvent.click(screen.getByRole("button", { name: "放弃草稿" }));
    fireEvent.click(screen.getByRole("button", { name: "恢复 rev-1" }));
    fireEvent.click(screen.getByRole("button", { name: "重新导演分镜" }));

    expect(mocks.activate).toHaveBeenNthCalledWith(1, { revisionId: "rev-2" }, expect.any(Object));
    expect(mocks.abandon).toHaveBeenCalledWith({ revisionId: "rev-2" });
    expect(mocks.activate).toHaveBeenNthCalledWith(2, { revisionId: "rev-1" }, expect.any(Object));
    expect(mocks.create).toHaveBeenCalled();
  });

  it("continues to storyboard production only after successful activation", () => {
    const onActivated = vi.fn();
    render(<DirectorReviewWorkbench project="demo" episode={1} onClose={vi.fn()} onActivated={onActivated} />);
    fireEvent.click(screen.getByRole("button", { name: "激活此版本" }));
    expect(onActivated).not.toHaveBeenCalled();
    const callbacks = mocks.activate.mock.calls[0][1];
    expect(callbacks).toBeDefined();
    callbacks.onSuccess({ ok: false });
    expect(onActivated).not.toHaveBeenCalled();
    callbacks.onSuccess({ ok: true, data: { ...candidate, status: "active" } });
    expect(onActivated).toHaveBeenCalledTimes(1);
  });

  it("lets an already active version continue without activating it again", () => {
    const onActivated = vi.fn();
    render(<DirectorReviewWorkbench project="demo" episode={1} onClose={vi.fn()} onActivated={onActivated} />);
    fireEvent.click(screen.getByText("当前生效").closest("button")!);
    fireEvent.click(screen.getByRole("button", { name: "进入分镜生图" }));
    expect(onActivated).toHaveBeenCalledTimes(1);
    expect(mocks.activate).not.toHaveBeenCalled();
  });
});
