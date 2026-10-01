import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const repair = vi.hoisted(() => ({ mutateAsync: vi.fn().mockResolvedValue({ scope: "revision:1:semantic:sem-1" }), isPending: false }));
const activate = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));
const edit = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));
const idle = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));
const repairTask = vi.hoisted(() => ({ started: false, start: vi.fn(), stream: { status: "idle", currentTask: "", error: null } }));
const state = vi.hoisted(() => ({ failed: false, passed: false, activeRevisionId: null as string | null, mode: "repair" as "repair" | "evidence" }));
const directorState = vi.hoisted(() => ({ revisions: [] as Array<Record<string, unknown>> }));
const revision = {
  revision_id: "sem-1", parent_revision_id: null, episode: 1, source_revision: 1,
  source_hash: "hash", version: 1, status: "review_required" as const,
  scenes: [{ id: "scene-1", ordinal: 1, source_range: { start_line: 1, end_line: 3 }, heading: "广播站", location: "广播站", time_of_day: "深夜", characters: [], blocks: [], content_hash: "h", status: "validated" as const }],
  beats: [], metadata_blocks: [], invalidated_beat_ids: [],
  validation_report: { get passed() { return state.passed; }, issues: [{ code: "missing_result", message: "缺少可见结果", severity: "error" as const, location: null, scene_id: "scene-1", beat_id: null, source_range: null }], version: 1 },
  created_at: "2026-09-01T00:00:00Z", activated_at: null,
};
const evidenceRevision = {
  revision_id: "sem-1", parent_revision_id: null, episode: 1,
  source_revision: 1, source_hash: "hash", version: 1, status: "review_required" as const,
  scenes: [{ id: "scene-1", ordinal: 1, source_range: { start_line: 7, end_line: 12 }, heading: "1-1 广播站 深夜 内", location: "广播站", time_of_day: "深夜", characters: ["林默"], content_hash: "scene-hash", status: "validated" as const, blocks: [
    { id: "line-8", ordinal: 1, kind: "action" as const, text: "△林默撞门。", source_range: { start_line: 8, end_line: 8 } },
    { id: "line-12", ordinal: 2, kind: "action" as const, text: "△门锁弹开。", source_range: { start_line: 12, end_line: 12 } },
  ] }],
  beats: [{ id: "beat-1", ordinal: 1, scene_id: "scene-1", source_ranges: [{ start_line: 8, end_line: 12 }], characters: ["林默"], goal: "进门", obstacle: "门锁", action: "撞门", reaction: "门框震动", turn: "门锁弹开", result: "林默停步", emotional_shift: "急迫转警惕", dialogue_source_ids: [], estimated_duration_seconds: 6, must_show: ["撞门"], script_facts: ["林默撞门", "门锁弹开"], director_interpretation: ["近景强调迟疑"], stale: false, stale_reason: null }],
  metadata_blocks: [{ id: "line-2", ordinal: 1, kind: "frontmatter" as const, text: "duration_seconds: 110", source_range: { start_line: 2, end_line: 2 } }],
  invalidated_beat_ids: [], validation_report: { passed: true, issues: [], version: 1 }, created_at: "2026-09-01T00:00:00Z", activated_at: null,
};

vi.mock("@/lib/queries/screenplay-semantics", () => ({
  screenplaySemanticKeys: { all: (project: string, episode: number) => ["projects", project, "episodes", episode, "screenplay-semantics"] },
  useScreenplaySemantics: () => ({ isLoading: false, isError: state.failed, error: new Error("连接中断"), refetch: vi.fn(), data: state.failed ? undefined : { ok: true, data: { active_revision_id: state.activeRevisionId, revisions: [state.mode === "repair" ? revision : evidenceRevision] } } }),
  useCreateScreenplaySemantics: () => idle,
  useRetrySemanticScene: () => idle,
  useEditScreenplaySemantics: () => edit,
  useRepairScreenplaySemantics: () => repair,
  useActivateScreenplaySemantics: () => activate,
}));
vi.mock("@/lib/queries/director-plans", () => ({
  useCreateDirectorPlan: () => idle,
  useDirectorPlans: () => ({
    isLoading: false,
    data: { ok: true, data: directorState.revisions },
  }),
}));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to }: { children?: React.ReactNode; to?: string }) => <a href={to}>{children}</a>,
}));
vi.mock("@/hooks/use-task-controller", () => ({ useTaskController: () => repairTask }));

import { ScreenplayWorkbench } from "@/components/episode/screenplay-workbench";

describe("ScreenplayWorkbench runtime repair gate", () => {
  beforeEach(() => { state.failed = false; state.passed = false; state.activeRevisionId = null; state.mode = "repair"; directorState.revisions = []; vi.clearAllMocks(); });

  it("offers repair for a failed revision and preserves the required action order", async () => {
    render(<ScreenplayWorkbench project="demo" episode={1} />);
    const actions = screen.getByLabelText("导演拆解操作").querySelectorAll("button");
    expect([...actions].map((button) => button.textContent)).toEqual(["解析场次", "自动修复问题", "确认此版校对", "生成镜头方案"]);
    await userEvent.click(screen.getByRole("button", { name: "自动修复问题" }));
    expect(repair.mutateAsync).toHaveBeenCalledWith({ revisionId: "sem-1" });
    expect(repairTask.start).toHaveBeenCalledWith({ scope: "revision:1:semantic:sem-1" });
    expect(screen.getByRole("button", { name: "确认此版校对" })).toBeDisabled();
    expect(screen.getByText("当前拆解仍有 1 个校验问题，请先修复并重新校验")).toBeVisible();
  });

  it("keeps correction actions and validation warnings in compact mode without duplicating stage navigation", () => {
    render(<ScreenplayWorkbench project="demo" episode={1} compact />);
    expect(screen.queryByRole("list", { name: "脚本制作阶段" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "自动修复问题" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "确认此版校对" })).toBeDisabled();
    expect(screen.getByText("缺少可见结果")).toBeVisible();
    expect(screen.getByLabelText("场次列表")).toBeVisible();
    expect(screen.getByLabelText("原文证据")).toBeVisible();
  });

  it("only unlocks manual activation after validation passes", async () => {
    state.passed = true;
    render(<ScreenplayWorkbench project="demo" episode={1} />);
    expect(activate.mutate).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "确认此版校对" }));
    expect(activate.mutate).toHaveBeenCalledWith({ revisionId: "sem-1" });
  });

  it("shows the four production stages and the reason generation is unavailable", () => {
    render(<ScreenplayWorkbench project="demo" episode={1} />);
    expect(screen.getByRole("list", { name: "脚本制作阶段" }).children).toHaveLength(4);
    expect(screen.getByText("请先确认校对版本，再生成镜头方案")).toBeVisible();
    expect(screen.getByRole("button", { name: "生成镜头方案" })).toBeDisabled();
  });

  it("shows load failure instead of presenting an empty script and generation action", () => {
    state.failed = true;
    render(<ScreenplayWorkbench project="demo" episode={1} />);
    expect(screen.getByRole("alert")).toHaveTextContent("连接中断");
    expect(screen.getByRole("button", { name: "重新加载" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "解析场次" })).not.toBeInTheDocument();
  });

  it("keeps a new draft at proofreading even when an older revision has a director plan", () => {
    state.activeRevisionId = "sem-older";
    directorState.revisions = [{ revision_id: "plan-old", semantic_revision_id: "sem-older", status: "active", groups: [] }];
    render(<ScreenplayWorkbench project="demo" episode={1} />);
    expect(screen.getByRole("list", { name: "脚本制作阶段" }).querySelector('[aria-current="step"]')).toHaveTextContent("校对并确认");
  });

  it("keeps a newly confirmed revision at generation when the plan belongs to an older revision", () => {
    state.activeRevisionId = "sem-1";
    directorState.revisions = [{ revision_id: "plan-old", semantic_revision_id: "sem-older", status: "review_required", groups: [] }];
    render(<ScreenplayWorkbench project="demo" episode={1} />);
    expect(screen.getByRole("list", { name: "脚本制作阶段" }).querySelector('[aria-current="step"]')).toHaveTextContent("生成镜头方案");
  });

  it.each(["review_required", "active"])("shows final stage for a matching %s director plan", (status) => {
    state.activeRevisionId = "sem-1";
    directorState.revisions = [{ revision_id: "plan-current", semantic_revision_id: "sem-1", status, groups: [] }];
    render(<ScreenplayWorkbench project="demo" episode={1} />);
    expect(screen.getByRole("list", { name: "脚本制作阶段" }).querySelector('[aria-current="step"]')).toHaveTextContent("审核镜头方案");
  });

  it("surfaces a completed director plan that requires review", () => {
    directorState.revisions = [{
      revision_id: "director-1",
      status: "review_required",
      groups: [
        { id: "group-1", shots: [{ id: "shot-1" }, { id: "shot-2" }] },
        { id: "group-2", shots: [{ id: "shot-3" }] },
      ],
    }];

    render(<ScreenplayWorkbench project="demo" episode={1} />);

    expect(screen.getByRole("status")).toHaveTextContent("镜头方案已生成，待人工审核");
    expect(screen.getByRole("status")).toHaveTextContent("2 个叙事组 · 3 个镜头");
    expect(screen.getByRole("link", { name: "审核镜头方案" })).toBeInTheDocument();
  });
});

describe("ScreenplayWorkbench evidence editing", () => {
  beforeEach(() => { state.failed = false; state.mode = "evidence"; state.activeRevisionId = null; directorState.revisions = []; vi.clearAllMocks(); });

  it("shows only selected beat evidence and keeps frontmatter out", () => {
    render(<ScreenplayWorkbench project="demo" episode={1} />);
    const evidence = screen.getByLabelText("原文证据");
    expect(evidence).toHaveTextContent("第 8-12 行");
    expect(evidence).toHaveTextContent("门锁弹开");
    expect(evidence).not.toHaveTextContent("duration_seconds");
  });

  it("splits a beat through semantic editing only", async () => {
    render(<ScreenplayWorkbench project="demo" episode={1} />);
    await userEvent.click(screen.getByRole("button", { name: "拆分节拍" }));
    expect(edit.mutate).toHaveBeenCalledWith({ revisionId: "sem-1", command: { type: "split", beat_id: "beat-1", before_line: 11 } });
  });
});
