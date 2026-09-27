import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DraftManager } from "./draft-manager";
import { defaultSettings } from "./settings";
import type { ScriptDocument } from "./types";

const api = vi.hoisted(() => ({ listGenerations: vi.fn(), startGeneration: vi.fn(),
  getGeneration: vi.fn(), retryGeneration: vi.fn(), rebaseGeneration: vi.fn(), getCandidate: vi.fn() }));
vi.mock("./api", () => ({ scriptCreationApi: api }));
import { GenerationPanel } from "./generation-panel";

function doc(id: string, kind: ScriptDocument["kind"], markdown: string, episode: number | null = null): ScriptDocument {
  return { id, kind, title: id, episode_number: episode, current_revision_id: "r-" + id,
    adopted_revision_id: null, source_origin: null, created_at: "", updated_at: "",
    revision: { id: "r-" + id, document_id: id, parent_revision_id: null, markdown,
      blocks: [], client_mutation_id: "", created_at: "", restored_from_revision_id: null } };
}

beforeEach(() => { vi.clearAllMocks(); api.listGenerations.mockResolvedValue([]); });

describe("GenerationPanel", () => {
  it("submits saved brief to the queued bootstrap API", async () => {
    const user = userEvent.setup();
    const brief = doc("brief", "brief", "故事想法已保存");
    const manager = new DraftManager(vi.fn().mockImplementation(async (_id, _revision, markdown) => doc("brief", "brief", markdown))); manager.load(brief);
    const run = { id: "run-1", status: "pending", steps: [{ key: "outline:0", title: "故事大纲", status: "pending", output: null }] };
    api.startGeneration.mockResolvedValue({ run, task_id: "task-1" });
    api.getGeneration.mockResolvedValue(run);
    render(<GenerationPanel project="demo" documents={[brief]} manager={manager} settings={defaultSettings()}
      selected={brief} instruction="节奏克制" onSelect={vi.fn()} onRefresh={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "生成故事框架与首集" }));
    await waitFor(() => expect(api.startGeneration).toHaveBeenCalledWith("demo", expect.objectContaining({
      mode: "bootstrap", brief_id: "brief", script_mode: "series", episode_count: 3,
      instruction: "节奏克制", client_mutation_id: expect.any(String),
    })));
    expect(await screen.findByText("故事大纲")).toBeInTheDocument();
    manager.dispose();
  });

  it("blocks generation while a local document has unsaved changes", async () => {
    const brief = doc("brief", "brief", "故事想法已保存");
    const manager = new DraftManager(vi.fn().mockImplementation(async (_id, _revision, markdown) => doc("brief", "brief", markdown))); manager.load(brief); manager.edit("brief", "本地未保存");
    render(<GenerationPanel project="demo" documents={[brief]} manager={manager} settings={defaultSettings()}
      selected={brief} instruction="" onSelect={vi.fn()} onRefresh={vi.fn()} />);
    expect(await screen.findByRole("button", { name: "生成故事框架与首集" })).toBeDisabled();
    expect(screen.getByText(/先保存并解决文档冲突/)).toBeInTheDocument();
    manager.dispose();
  });

  it("shows model configuration failure as an actionable error", async () => {
    const user = userEvent.setup();
    const brief = doc("brief", "brief", "故事想法已保存");
    const manager = new DraftManager(vi.fn().mockImplementation(async (_id, _revision, markdown) => doc("brief", "brief", markdown))); manager.load(brief);
    api.startGeneration.mockRejectedValue(new Error("未配置剧本创作模型"));
    render(<GenerationPanel project="demo" documents={[brief]} manager={manager} settings={defaultSettings()}
      selected={brief} instruction="" onSelect={vi.fn()} onRefresh={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "生成故事框架与首集" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("未配置剧本创作模型");
    manager.dispose();
  });
});

describe("GenerationPanel retry", () => {
  it("keeps polling after a failed run is requeued", async () => {
    const user = userEvent.setup();
    const brief = doc("brief", "brief", "故事想法已保存");
    const manager = new DraftManager(vi.fn().mockImplementation(async (_id, _revision, markdown) => doc("brief", "brief", markdown)));
    const failed = { id: "run-1", status: "failed", error: "model unavailable", steps: [{ key: "outline:0", title: "故事大纲", status: "failed", output: null }] };
    api.listGenerations.mockResolvedValue([failed]);
    api.retryGeneration.mockResolvedValue({ run: failed, task_id: "new-task" });
    api.getGeneration.mockResolvedValue({ ...failed, status: "running", error: null });
    render(<GenerationPanel project="demo" documents={[brief]} manager={manager} settings={defaultSettings()}
      selected={brief} instruction="" onSelect={vi.fn()} onRefresh={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "继续未完成步骤" }));
    await waitFor(() => expect(api.getGeneration).toHaveBeenCalledWith("demo", "run-1"));
    manager.dispose();
  });
});
