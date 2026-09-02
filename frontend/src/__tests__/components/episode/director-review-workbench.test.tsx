import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  edit: vi.fn(),
  activate: vi.fn(),
  abandon: vi.fn(),
  migrate: vi.fn(),
}));

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
    useEditDirectorPlan: () => ({ mutate: mocks.edit, isPending: false }),
    useActivateDirectorPlan: () => ({ mutate: mocks.activate, isPending: false }),
    useAbandonDirectorPlan: () => ({ mutate: mocks.abandon, isPending: false }),
    useUpdateDirectorPlanMigration: () => ({ mutate: mocks.migrate, isPending: false }),
  };
});

import type { DirectorPlanRevision } from "@/lib/queries/director-plans";
import { DirectorReviewWorkbench } from "@/components/episode/director-review/director-review-workbench";

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

  it("shows revision history, real stages and old/new narrative structures", () => {
    render(<DirectorReviewWorkbench project="demo" episode={1} onClose={vi.fn()} />);

    expect(screen.getByRole("button", { name: "重新导演分镜" })).toBeInTheDocument();
    expect(screen.getByText("当前生效")).toBeInTheDocument();
    expect(screen.getAllByText("待人工审核")).not.toHaveLength(0);
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

    expect(screen.getByText("高置信度")).toBeInTheDocument();
    expect(screen.getByText("中置信度")).toBeInTheDocument();
    expect(screen.getByText("低置信度")).toBeInTheDocument();
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

    expect(mocks.activate).toHaveBeenNthCalledWith(1, { revisionId: "rev-2" });
    expect(mocks.abandon).toHaveBeenCalledWith({ revisionId: "rev-2" });
    expect(mocks.activate).toHaveBeenNthCalledWith(2, { revisionId: "rev-1" });
    expect(mocks.create).toHaveBeenCalled();
  });
});
