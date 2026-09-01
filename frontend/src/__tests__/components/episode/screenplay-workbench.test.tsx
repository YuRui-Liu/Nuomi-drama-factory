import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const repair = vi.hoisted(() => ({ mutateAsync: vi.fn().mockResolvedValue({ scope: "revision:1:semantic:sem-1" }), isPending: false }));
const activate = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));
const idle = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));
const repairTask = vi.hoisted(() => ({ started: false, start: vi.fn(), stream: { status: "idle", currentTask: "", error: null } }));
const state = vi.hoisted(() => ({ passed: false, activeRevisionId: null as string | null }));
const revision = {
  revision_id: "sem-1", parent_revision_id: null, episode: 1, source_revision: 1,
  source_hash: "hash", version: 1, status: "review_required" as const,
  scenes: [{ id: "scene-1", ordinal: 1, source_range: { start_line: 1, end_line: 3 }, heading: "广播站", location: "广播站", time_of_day: "深夜", characters: [], blocks: [], content_hash: "h", status: "validated" as const }],
  beats: [], metadata_blocks: [], invalidated_beat_ids: [],
  validation_report: { get passed() { return state.passed; }, issues: [{ code: "missing_result", message: "缺少可见结果", severity: "error" as const, location: null, scene_id: "scene-1", beat_id: null, source_range: null }], version: 1 },
  created_at: "2026-09-01T00:00:00Z", activated_at: null,
};

vi.mock("@/lib/queries/screenplay-semantics", () => ({
  screenplaySemanticKeys: { all: (project: string, episode: number) => ["projects", project, "episodes", episode, "screenplay-semantics"] },
  useScreenplaySemantics: () => ({ isLoading: false, data: { ok: true, data: { active_revision_id: state.activeRevisionId, revisions: [revision] } } }),
  useCreateScreenplaySemantics: () => idle,
  useRetrySemanticScene: () => idle,
  useEditScreenplaySemantics: () => idle,
  useRepairScreenplaySemantics: () => repair,
  useActivateScreenplaySemantics: () => activate,
}));
vi.mock("@/lib/queries/director-plans", () => ({ useCreateDirectorPlan: () => idle }));
vi.mock("@/hooks/use-task-controller", () => ({ useTaskController: () => repairTask }));

import { ScreenplayWorkbench } from "@/components/episode/screenplay-workbench";

describe("ScreenplayWorkbench runtime repair gate", () => {
  beforeEach(() => { state.passed = false; state.activeRevisionId = null; vi.clearAllMocks(); });

  it("offers repair for a failed revision and preserves the required action order", async () => {
    render(<ScreenplayWorkbench project="demo" episode={1} />);
    const actions = screen.getByLabelText("导演拆解操作").querySelectorAll("button");
    expect([...actions].map((button) => button.textContent)).toEqual(["解析场次", "委托 Runtime 修复", "激活拆解", "生成镜头方案"]);
    await userEvent.click(screen.getByRole("button", { name: "委托 Runtime 修复" }));
    expect(repair.mutateAsync).toHaveBeenCalledWith({ revisionId: "sem-1" });
    expect(repairTask.start).toHaveBeenCalledWith({ scope: "revision:1:semantic:sem-1" });
    expect(screen.getByRole("button", { name: "激活拆解" })).toHaveAttribute("title", "当前拆解仍有 1 个校验问题，请先修复并重新校验");
  });

  it("only unlocks manual activation after validation passes", async () => {
    state.passed = true;
    render(<ScreenplayWorkbench project="demo" episode={1} />);
    expect(activate.mutate).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "激活拆解" }));
    expect(activate.mutate).toHaveBeenCalledWith({ revisionId: "sem-1" });
  });
});
