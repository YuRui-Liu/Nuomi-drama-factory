import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ScriptDocument } from "./types";

const api = vi.hoisted(() => ({
  listEntities: vi.fn(), listConsistencyRuns: vi.fn(), listHandoffs: vi.fn(),
  prepareHandoff: vi.fn(), confirmHandoff: vi.fn(), getHandoff: vi.fn(), retryHandoff: vi.fn(),
}));
vi.mock("./api", () => ({ scriptCreationApi: api }));
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
    affected_nonupdated_scene_ids: [], available_scene_ids: ["scene-1"], needs_reparse: false,
    inferred_impacts: ["scene_semantics"] },
};
function renderDialog(onRefresh = vi.fn()) {
  return render(<HandoffDialog project="demo" document={script} documents={[script, design]}
    allSaved onClose={vi.fn()} onRefresh={onRefresh} />);
}
beforeEach(() => {
  vi.clearAllMocks();
  api.listEntities.mockResolvedValue([]);
  api.listConsistencyRuns.mockResolvedValue([]);
  api.listHandoffs.mockResolvedValue([]);
  api.prepareHandoff.mockResolvedValue(prepared);
  api.confirmHandoff.mockResolvedValue({ ...prepared, status: "completed", source_revision: "source-1" });
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
    await user.click(screen.getByRole("checkbox", { name: /scene-1/ }));
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
    await user.click(await screen.findByRole("button", { name: "重试原交接" }));
    await waitFor(() => expect(api.retryHandoff).toHaveBeenCalledWith("demo", "h1"));
    expect(api.prepareHandoff).not.toHaveBeenCalled();
    expect(api.confirmHandoff).not.toHaveBeenCalled();
  });

});
