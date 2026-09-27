import { render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DraftManager } from "./draft-manager";
import { headingsForMarkdown } from "./document-tree";
import type { ScriptDocument } from "./types";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("./api", () => ({ scriptCreationApi: { get: mocks.get } }));
import { DocumentEditor } from "./document-editor";

function document(revision: string, markdown: string): ScriptDocument {
  return { id: "one", kind: "outline", title: "故事大纲", episode_number: null,
    current_revision_id: revision, adopted_revision_id: null, source_origin: null,
    created_at: "", updated_at: "",
    revision: { id: revision, document_id: "one", parent_revision_id: null,
      markdown, blocks: [{ id: "block", markdown }], client_mutation_id: "",
      created_at: "", restored_from_revision_id: null } };
}
const conflict = () => Object.assign(new Error("revision conflict"), { status: 409 });

describe("DocumentEditor conflict comparison", () => {
  it("assigns every heading its source codepoint anchor under StrictMode", () => {
    const markdown = "<!-- nuomi-script-settings\n{}\n-->\n\n# 人物小传\n\n## 林川\n\n### 人物弧光";
    const manager = new DraftManager(vi.fn());
    manager.load(document("r1", markdown));
    render(<StrictMode><DocumentEditor project="demo" draft={manager.get("one")!} manager={manager} onSelection={() => {}} /></StrictMode>);
    const anchors = headingsForMarkdown(markdown).map((heading) => "heading-one-" + heading.anchor);
    expect(screen.getByRole("heading", { name: "林川" })).toHaveAttribute("id", anchors[0]);
    expect(screen.getByRole("heading", { name: "人物弧光" })).toHaveAttribute("id", anchors[1]);
    manager.dispose();
  });

  it("saves against the revision shown in comparison, even if remote advances again", async () => {
    const user = userEvent.setup();
    const save = vi.fn().mockRejectedValue(conflict());
    const manager = new DraftManager(save);
    manager.load(document("r1", "old"));
    manager.edit("one", "my draft");
    await manager.flush("one");
    mocks.get.mockReset().mockResolvedValueOnce(document("r2", "other writer")).mockResolvedValueOnce(document("r3", "unseen update"));
    render(<DocumentEditor project="demo" draft={manager.get("one")!} manager={manager} onSelection={() => {}} />);
    await user.click(screen.getByRole("button", { name: "比较最新版本" }));
    expect(await screen.findByText("other writer")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "保留我的修改并保存" }));
    await waitFor(() => expect(save).toHaveBeenLastCalledWith("one", "r2", "my draft"));
    expect(mocks.get).toHaveBeenCalledTimes(1);
    manager.dispose();
  });

  it("does not enable resolution when latest revision cannot be read", async () => {
    const user = userEvent.setup();
    const manager = new DraftManager(vi.fn().mockRejectedValue(conflict()));
    manager.load(document("r1", "old"));
    manager.edit("one", "my draft");
    await manager.flush("one");
    mocks.get.mockReset().mockRejectedValue(new Error("offline"));
    render(<DocumentEditor project="demo" draft={manager.get("one")!} manager={manager} onSelection={() => {}} />);
    await user.click(screen.getByRole("button", { name: "比较最新版本" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("最新版本读取失败");
    expect(screen.queryByRole("button", { name: "保留我的修改并保存" })).not.toBeInTheDocument();
    manager.dispose();
  });
});
