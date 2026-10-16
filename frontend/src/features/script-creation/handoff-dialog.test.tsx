import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ScriptDocument } from "./types";

const api = vi.hoisted(() => ({
  listEntities: vi.fn(), listConsistencyRuns: vi.fn(), listHandoffs: vi.fn(),
  prepareHandoff: vi.fn(), confirmHandoff: vi.fn(), getHandoff: vi.fn(), retryHandoff: vi.fn(),
  openHandoffCanvas: vi.fn(),
}));
const projectApi = vi.hoisted(() => ({ getSupertaleProject: vi.fn() }));
const navigation = vi.hoisted(() => ({ router: null as any }));
vi.mock("./api", () => ({ scriptCreationApi: api }));
vi.mock("@/api/projects", () => projectApi);
vi.mock("@/lib/app-router", () => ({ getAppRouter: () => navigation.router }));
import { HandoffDialog } from "./handoff-dialog";

const script: ScriptDocument = {
  id: "episode", kind: "episode_script", title: "第 1 集", episode_number: 1,
  current_revision_id: "r1", adopted_revision_id: null, source_origin: null,
  created_at: "", updated_at: "", revision: { id: "r1", document_id: "episode",
    parent_revision_id: null, markdown: "# 正文\n\n## 1-1 内景", blocks: [],
    client_mutation_id: "create", created_at: "", restored_from_revision_id: null },
};
const design: ScriptDocument = { ...script, id: "people", kind: "people", title: "人物小传",
  episode_number: null, current_revision_id: "p1", revision: { ...script.revision,
    id: "p1", document_id: "people", markdown: "# 人物" } };
const prepared = {
  id: "h1", status: "prepared", project_id: "demo", episode_number: 1,
  document_id: "episode", revision_id: "r1", expected_source_project_revision: 3,
  source_revision: null, source_hash: null, task_id: null, task_result: null, error: null,
  created_at: "", updated_at: "", snapshot: { document_id: "episode", revision_id: "r1",
    episode_number: 1, title: "第 1 集", markdown: script.revision.markdown,
    reference_revisions: { people: "p1" }, references: [], entities: [],
    fact_acknowledgement: { mode: "unchecked", reason: "已人工核对", run_id: null, known_fact_issues: {} },
    update_scope: { mode: "all", scene_ids: [] }, previous_source: null, previous_stage_revisions: {} },
  diff: { text: [{ operation: "insert", old_lines: [1, 0], new_lines: [1, 3], before: [], after: ["# 正文"] }],
    scenes: [], dialogue: [], references: [], entity_references: [], reused_scenes: [],
    affected_nonupdated_scene_ids: [], available_scene_ids: ["scene-1"],
    available_scenes: [{ id: "scene-1", heading: "1-1 内景", location: "内景" }], needs_reparse: false,
    inferred_impacts: ["scene_semantics"] },
};
function renderDialog(onRefresh = vi.fn()) {
  return render(<HandoffDialog project="demo" document={script} documents={[script, design]}
    allSaved onClose={vi.fn()} onRefresh={onRefresh} />);
}
beforeEach(() => {
  vi.clearAllMocks();
  navigation.router = null;
  projectApi.getSupertaleProject.mockResolvedValue({ creation_mode: "agent_canvas_v1" });
  api.listEntities.mockResolvedValue([]);
  api.listConsistencyRuns.mockResolvedValue([]);
  api.listHandoffs.mockResolvedValue([]);
  api.prepareHandoff.mockResolvedValue(prepared);
  api.confirmHandoff.mockResolvedValue({ ...prepared, status: "completed", source_revision: 42 });
  api.openHandoffCanvas.mockResolvedValue({ episode_id: "managed-ep-1", canvas_id: "canvas-1", status: "applied", operation_id: "op-1" });
});

describe("production handoff", () => {
  it("requires an explicit reference and unchecked-facts reason before preview, then separate confirmation", async () => {
    const user = userEvent.setup();
    renderDialog();
    await screen.findByText("选择交接范围");
    expect(screen.getByRole("button", { name: "预览交接差异" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /人物小传/ }));
    await user.type(screen.getByRole("textbox", { name: "未运行关联检查的说明" }), "已人工核对");
    await user.click(screen.getByRole("button", { name: "预览交接差异" }));
    await waitFor(() => expect(api.prepareHandoff).toHaveBeenCalledWith("demo", expect.objectContaining({
      document_id: "episode", revision_id: "r1", reference_revisions: { people: "p1" },
      selected_entity_ids: [], update_scope: { mode: "all", scene_ids: [] },
      fact_acknowledgement: expect.objectContaining({ mode: "unchecked", reason: "已人工核对" }),
    })));
    expect(api.confirmHandoff).not.toHaveBeenCalled();
    expect(await screen.findByText("实际文本修改")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认本集并交接制作" }));
    await waitFor(() => expect(api.confirmHandoff).toHaveBeenCalledWith("demo", "h1", {
      expected_source_project_revision: 3, client_mutation_id: expect.any(String),
    }));
    expect(await screen.findByRole("button", { name: "打开分集画布" })).toBeInTheDocument();
  });

  it("shows a canvas entry for confirmed history only in agent_canvas_v1 projects", async () => {
    api.listHandoffs.mockResolvedValue([{ ...prepared, status: "dispatched", source_revision: 42 }]);
    const view = renderDialog();
    expect(await screen.findByRole("button", { name: "打开分集画布" })).toBeInTheDocument();

    view.unmount();
    projectApi.getSupertaleProject.mockResolvedValue({ creation_mode: "legacy" });
    renderDialog();
    await screen.findByText("选择交接范围");
    expect(screen.queryByRole("button", { name: "打开分集画布" })).not.toBeInTheDocument();
  });

  it("does not offer a canvas entry for a completed legacy handoff without a confirmed source", async () => {
    api.listHandoffs.mockResolvedValue([{ ...prepared, status: "completed", source_revision: null }]);
    renderDialog();
    await screen.findByText("选择交接范围");
    expect(screen.queryByRole("button", { name: "打开分集画布" })).not.toBeInTheDocument();
  });

  it("does not treat a zero numeric source revision as confirmed", async () => {
    api.listHandoffs.mockResolvedValue([{ ...prepared, status: "dispatched", source_revision: 0 }]);
    renderDialog();
    await screen.findByText("选择交接范围");
    expect(screen.queryByRole("button", { name: "打开分集画布" })).not.toBeInTheDocument();
  });

  it("opens the returned canvas link using the confirmed handoff ID", async () => {
    const user = userEvent.setup();
    api.listHandoffs.mockResolvedValue([{ ...prepared, status: "dispatched", source_revision: 42 }]);
    renderDialog();
    await user.click(await screen.findByRole("button", { name: "打开分集画布" }));
    expect(await screen.findByRole("link", { name: "进入分集画布" })).toHaveAttribute(
      "href", "/projects/demo/freezone?workspace=canvas-1",
    );
    expect(api.openHandoffCanvas).toHaveBeenCalledWith("demo", "h1");
  });

  it("shows bridge conflicts and allows retrying the same confirmed handoff", async () => {
    const user = userEvent.setup();
    api.listHandoffs.mockResolvedValue([{ ...prepared, status: "dispatched", source_revision: "source-1" }]);
    api.openHandoffCanvas.mockRejectedValueOnce(Object.assign(new Error("同号分集已绑定其他来源"), { status: 409 }));
    renderDialog();
    await user.click(await screen.findByRole("button", { name: "打开分集画布" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("同号分集已绑定其他来源");
    await user.click(screen.getByRole("button", { name: "重试打开分集画布" }));
    expect(await screen.findByRole("link", { name: "进入分集画布" })).toHaveAttribute(
      "href", "/projects/demo/freezone?workspace=canvas-1",
    );
    expect(api.openHandoffCanvas).toHaveBeenNthCalledWith(1, "demo", "h1");
    expect(api.openHandoffCanvas).toHaveBeenNthCalledWith(2, "demo", "h1");
  });

  it("does not navigate for a non-applied bridge result and prevents duplicate requests", async () => {
    const user = userEvent.setup();
    let resolveBridge!: (value: { episode_id: string; canvas_id: string; status: string; operation_id: string }) => void;
    api.openHandoffCanvas.mockReturnValueOnce(new Promise(resolve => { resolveBridge = resolve; }));
    api.listHandoffs.mockResolvedValue([{ ...prepared, status: "dispatched", source_revision: "source-1" }]);
    renderDialog();
    const openButton = await screen.findByRole("button", { name: "打开分集画布" });
    await user.click(openButton);
    await user.click(openButton);
    expect(api.openHandoffCanvas).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "正在准备分集画布…" })).toBeDisabled();
    resolveBridge({ episode_id: "managed-ep-1", canvas_id: "canvas-1", status: "running", operation_id: "op-1" });
    expect(await screen.findByRole("alert")).toHaveTextContent("画布尚未就绪");
    expect(screen.queryByRole("link", { name: "进入分集画布" })).not.toBeInTheDocument();
  });

  it("ignores a late canvas response after navigation leaves the source project", async () => {
    const user = userEvent.setup();
    const origin = { pathname: "/projects/demo/creation", search: {}, href: "/projects/demo/creation" };
    const destination = { pathname: "/projects/other/creation", search: {}, href: "/projects/other/creation" };
    navigation.router = { state: { location: origin }, navigate: vi.fn() };
    let resolveBridge!: (value: { episode_id: string; canvas_id: string; status: string; operation_id: string }) => void;
    api.openHandoffCanvas.mockReturnValueOnce(new Promise(resolve => { resolveBridge = resolve; }));
    api.listHandoffs.mockResolvedValue([{ ...prepared, status: "dispatched", source_revision: "source-1" }]);
    renderDialog();
    await user.click(await screen.findByRole("button", { name: "打开分集画布" }));
    navigation.router.state.location = destination;
    resolveBridge({ episode_id: "managed-ep-1", canvas_id: "canvas-1", status: "applied", operation_id: "op-1" });
    await waitFor(() => expect(api.openHandoffCanvas).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("link", { name: "进入分集画布" })).not.toBeInTheDocument();
    expect(navigation.router.navigate).not.toHaveBeenCalled();
  });

  it("resets canvas state when the active history preview changes on the same route", async () => {
    const user = userEvent.setup();
    const earlier = { ...prepared, id: "h1", status: "dispatched", source_revision: "source-1" };
    const later = { ...prepared, id: "h2", revision_id: "r2", status: "dispatched", source_revision: "source-2",
      snapshot: { ...prepared.snapshot, revision_id: "r2" } };
    api.listHandoffs.mockResolvedValue([later, earlier]);
    let resolveBridge!: (value: { episode_id: string; canvas_id: string; status: string; operation_id: string }) => void;
    api.openHandoffCanvas.mockReturnValueOnce(new Promise(resolve => { resolveBridge = resolve; }));
    renderDialog();

    const earlierRow = (await screen.findByText(/^r1 ·/)).parentElement!;
    await user.click(within(earlierRow).getByRole("button", { name: "查看快照" }));
    const earlierPreview = screen.getByText(/正文版本 r1/).closest("section")!;
    await user.click(within(earlierPreview).getByRole("button", { name: "打开分集画布" }));
    expect(screen.getByRole("button", { name: "正在准备分集画布…" })).toBeDisabled();

    const laterRow = screen.getByText(/^r2 ·/).parentElement!;
    await user.click(within(laterRow).getByRole("button", { name: "查看快照" }));
    const laterPreview = screen.getByText(/正文版本 r2/).closest("section")!;
    expect(within(laterPreview).getByRole("button", { name: "打开分集画布" })).toBeEnabled();
    expect(screen.queryByRole("link", { name: "进入分集画布" })).not.toBeInTheDocument();

    await act(async () => {
      resolveBridge({ episode_id: "managed-ep-1", canvas_id: "canvas-1", status: "applied", operation_id: "op-1" });
    });
    expect(screen.queryByRole("link", { name: "进入分集画布" })).not.toBeInTheDocument();
  });

  it("clears the prior canvas link when switching to another confirmed history preview", async () => {
    const user = userEvent.setup();
    const earlier = { ...prepared, id: "h1", status: "dispatched", source_revision: "source-1" };
    const later = { ...prepared, id: "h2", revision_id: "r2", status: "dispatched", source_revision: "source-2",
      snapshot: { ...prepared.snapshot, revision_id: "r2" } };
    api.listHandoffs.mockResolvedValue([later, earlier]);
    renderDialog();

    const earlierRow = (await screen.findByText(/^r1 ·/)).parentElement!;
    await user.click(within(earlierRow).getByRole("button", { name: "查看快照" }));
    const earlierPreview = screen.getByText(/正文版本 r1/).closest("section")!;
    await user.click(within(earlierPreview).getByRole("button", { name: "打开分集画布" }));
    expect(await screen.findByRole("link", { name: "进入分集画布" })).toHaveAttribute(
      "href", "/projects/demo/freezone?workspace=canvas-1",
    );

    const laterRow = screen.getByText(/^r2 ·/).parentElement!;
    await user.click(within(laterRow).getByRole("button", { name: "查看快照" }));
    const laterPreview = screen.getByText(/正文版本 r2/).closest("section")!;
    expect(screen.queryByRole("link", { name: "进入分集画布" })).not.toBeInTheDocument();
    expect(within(laterPreview).getByRole("button", { name: "打开分集画布" })).toBeEnabled();
  });

  it("invalidates preview when selected scope changes and only submits parser scene IDs", async () => {
    const user = userEvent.setup();
    renderDialog();
    await screen.findByText("选择交接范围");
    await user.type(screen.getByRole("textbox", { name: "未运行关联检查的说明" }), "已核对");
    await user.click(screen.getByRole("button", { name: "预览交接差异" }));
    await screen.findByRole("button", { name: "确认本集并交接制作" });
    await user.click(screen.getByRole("radio", { name: "仅校对所选场次" }));
    expect(screen.queryByRole("button", { name: "确认本集并交接制作" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: /1-1 内景/ }));
    await user.click(screen.getByRole("button", { name: "预览交接差异" }));
    await waitFor(() => expect(api.prepareHandoff).toHaveBeenLastCalledWith("demo", expect.objectContaining({
      update_scope: { mode: "selected", scene_ids: ["scene-1"] },
    })));
  });
  it("keeps the prepare mutation ID after a lost response", async () => {
    const user = userEvent.setup();
    api.prepareHandoff.mockRejectedValueOnce(new Error("response lost")).mockResolvedValueOnce(prepared);
    renderDialog();
    await screen.findByText("选择交接范围");
    await user.type(screen.getByRole("textbox", { name: "未运行关联检查的说明" }), "人工核对");
    await user.click(screen.getByRole("button", { name: "预览交接差异" }));
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "预览交接差异" }));
    await waitFor(() => expect(api.prepareHandoff).toHaveBeenCalledTimes(2));
    expect(api.prepareHandoff.mock.calls[0][1].client_mutation_id)
      .toBe(api.prepareHandoff.mock.calls[1][1].client_mutation_id);
  });

  it("requires a reason for each current factual issue even when its design document is not selected", async () => {
    const user = userEvent.setup();
    api.listConsistencyRuns.mockResolvedValue([{ id: "run1", episode_document_id: "episode",
      context_revisions: { episode: "r1", people: "p1" }, mode: "actual", proposal_id: null,
      hypothetical_document_id: null, status: "completed", task_id: null, error: null,
      issues: [{ id: "issue1", run_id: "run1", category: "fact", kind: "conflict",
        explanation: "人物年龄冲突", suggested_action: "核对", source: null, target: null,
        context_revisions: { episode: "r1", people: "p1" }, mode: "actual", proposal_id: null,
        stale: false, intentional_reason: null }] }]);
    renderDialog();
    await screen.findByText("选择交接范围");
    await user.type(screen.getByRole("textbox", { name: "未运行关联检查的说明" }), "人工核对");
    expect(screen.getByRole("button", { name: "预览交接差异" })).toBeDisabled();
    await user.type(screen.getByRole("textbox", { name: "事实问题 issue1 的处理说明" }), "保留设定差异，后续修订");
    await user.click(screen.getByRole("button", { name: "预览交接差异" }));
    await waitFor(() => expect(api.prepareHandoff).toHaveBeenCalledWith("demo", expect.objectContaining({
      reference_revisions: {}, fact_acknowledgement: expect.objectContaining({
        issue_reasons: { issue1: "保留设定差异，后续修订" },
      }),
    })));
  });

  it("retries a failed stored handoff without creating or confirming another", async () => {
    const user = userEvent.setup();
    api.listHandoffs.mockResolvedValue([{ ...prepared, status: "failed", error: "worker unavailable" }]);
    api.retryHandoff.mockResolvedValue({ ...prepared, status: "completed", source_revision: "source-1" });
    renderDialog();
    await user.click(await screen.findByRole("button", { name: "恢复/重试交接" }));
    await waitFor(() => expect(api.retryHandoff).toHaveBeenCalledWith("demo", "h1"));
    expect(api.prepareHandoff).not.toHaveBeenCalled();
    expect(api.confirmHandoff).not.toHaveBeenCalled();
  });

});


afterEach(() => vi.useRealTimers());

describe("handoff recovery and readable review", () => {
  it.each(["dispatching", "dispatched"])("offers safe recovery for a %s original record", async (status) => {
    const user = userEvent.setup();
    api.listHandoffs.mockResolvedValue([{ ...prepared, status }]);
    api.retryHandoff.mockResolvedValue({ ...prepared, status: "dispatched" });
    renderDialog();
    await user.click(await screen.findByRole("button", { name: "恢复/重试交接" }));
    expect(api.retryHandoff).toHaveBeenCalledWith("demo", "h1");
    expect(api.confirmHandoff).not.toHaveBeenCalled();
    expect(api.prepareHandoff).not.toHaveBeenCalled();
  });

  it("continues polling after a transient GET failure and stops after completion", async () => {
    vi.useFakeTimers();
    api.listHandoffs.mockResolvedValue([{ ...prepared, status: "dispatched" }]);
    api.getHandoff.mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({ ...prepared, status: "completed", source_revision: "source-1" });
    const view = renderDialog();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(api.getHandoff).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(api.getHandoff).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(api.getHandoff).toHaveBeenCalledTimes(2);
    view.unmount();
  });

  it("clears old selectable scenes when a new preview needs reparse", async () => {
    const user = userEvent.setup();
    api.prepareHandoff.mockResolvedValueOnce({ ...prepared, diff: { ...prepared.diff,
      available_scenes: [{ id: "scene-1", heading: "1-1 客厅 · 夜", location: "客厅" }] } })
      .mockResolvedValueOnce({ ...prepared, id: "h2", diff: { ...prepared.diff,
        available_scene_ids: [], available_scenes: [], needs_reparse: true } });
    renderDialog();
    await screen.findByText("选择交接范围");
    await user.type(screen.getByRole("textbox", { name: "未运行关联检查的说明" }), "人工核对");
    await user.click(screen.getByRole("button", { name: "预览交接差异" }));
    await screen.findByRole("button", { name: "确认本集并交接制作" });
    await user.click(screen.getByRole("radio", { name: "仅校对所选场次" }));
    expect(screen.getByRole("checkbox", { name: /1-1 客厅 · 夜/ })).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "校对本集全部场次" }));
    await user.click(screen.getByRole("button", { name: "预览交接差异" }));
    await waitFor(() => expect(api.prepareHandoff).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("radio", { name: "仅校对所选场次" })).toBeDisabled();
    expect(screen.queryByRole("checkbox", { name: /1-1 客厅 · 夜/ })).not.toBeInTheDocument();
  });

  it("shows removed text, readable scene and reference labels, and rule-based impact wording", async () => {
    const user = userEvent.setup();
    api.prepareHandoff.mockResolvedValue({ ...prepared, diff: { ...prepared.diff,
      text: [{ operation: "delete", old_lines: [1, 2], new_lines: [1, 0], before: ["原句一", "原句二"], after: [] }],
      scenes: [{ operation: "insert", old_scene_ids: [], new_scene_ids: ["scene-1"] }],
      references: [{ document_id: "people", before_revision: null, after_revision: "p1", operation: "selected" }],
      available_scenes: [{ id: "scene-1", heading: "1-1 客厅 · 夜", location: "客厅" }],
    } });
    renderDialog();
    await screen.findByText("选择交接范围");
    await user.type(screen.getByRole("textbox", { name: "未运行关联检查的说明" }), "人工核对");
    await user.click(screen.getByRole("button", { name: "预览交接差异" }));
    expect(await screen.findByText(/原句一\s+原句二/)).toBeInTheDocument();
    expect(screen.getByText(/新增.*1-1 客厅 · 夜/)).toBeInTheDocument();
    expect(screen.getByText(/人物小传.*新增/)).toBeInTheDocument();
    expect(screen.getByText(/场次校对可能受影响/)).toBeInTheDocument();
  });

  it("recovers a legacy completed record whose scene extraction failed", async () => {
    const user = userEvent.setup();
    api.listHandoffs.mockResolvedValue([{ ...prepared, status: "completed", task_result: {
      status: "review_required", failed_scenes: 1,
      validation_report: { issues: [{ code: "scene_extraction_failed", message: "场次提取失败" }] },
    } }]);
    api.retryHandoff.mockResolvedValue({ ...prepared, status: "dispatched" });
    renderDialog();
    expect(await screen.findByText(/场次提取失败/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "恢复/重试交接" }));
    expect(api.retryHandoff).toHaveBeenCalledWith("demo", "h1");
  });

  it("labels a successful review-required result as pending human review", async () => {
    api.listHandoffs.mockResolvedValue([{ ...prepared, status: "completed", task_result: {
      status: "review_required", failed_scenes: 0, validation_report: { issues: [] },
    } }]);
    renderDialog();
    expect(await screen.findByText(/已交接，校对待确认/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看校对结果" })).toBeInTheDocument();
  });

  it("uses a fresh prepare mutation after a confirm 409, while keeping retry stable for lost responses", async () => {
    const user = userEvent.setup();
    api.confirmHandoff.mockRejectedValueOnce(Object.assign(new Error("source changed"), { status: 409 }));
    renderDialog();
    await screen.findByText("选择交接范围");
    await user.type(screen.getByRole("textbox", { name: "未运行关联检查的说明" }), "人工核对");
    await user.click(screen.getByRole("button", { name: "预览交接差异" }));
    await user.click(await screen.findByRole("button", { name: "确认本集并交接制作" }));
    await screen.findByRole("alert");
    expect(screen.queryByRole("button", { name: "确认本集并交接制作" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "预览交接差异" }));
    await waitFor(() => expect(api.prepareHandoff).toHaveBeenCalledTimes(2));
    expect(api.prepareHandoff.mock.calls[0][1].client_mutation_id)
      .not.toBe(api.prepareHandoff.mock.calls[1][1].client_mutation_id);
  });
});
