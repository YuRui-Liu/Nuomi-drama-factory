import { render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DraftManager } from "./draft-manager";
import { headingsForMarkdown } from "./document-tree";
import { defaultSettings, encodeBriefSettings } from "./settings";
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
  it("renders saved brief settings into empty sections without replacing authored content or modifying source", async () => {
    const markdown = encodeBriefSettings({ ...defaultSettings(), idea: "主角名字不要套路。\n末日丧尸生存", audience: ["大众向", "烧脑推理"], genrePrimary: "末世丧尸生存", episodeCount: 60, style: ["3D国风动画"] }, "# 创作简报\n\n## 故事想法\n\n## 目标观众\n\n## 创作边界\n\n不要用旁白解释谜底。");
    const manager = new DraftManager(vi.fn());
    manager.load({ ...document("r1", markdown), kind: "brief", title: "创作简报" });
    const { container } = render(<DocumentEditor project="demo" draft={manager.get("one")!} manager={manager} onSelection={() => {}} />);
    expect(container).toHaveTextContent("末日丧尸生存");
    expect(container).toHaveTextContent("大众向、烧脑推理");
    expect(container).toHaveTextContent("60 集");
    expect(container).toHaveTextContent("3D国风动画");
    expect(container).toHaveTextContent("不要用旁白解释谜底。");
    expect(container).not.toHaveTextContent("nuomi-script-settings");
    expect(screen.getAllByRole("heading", { name: "创作简报" })).toHaveLength(1);
    expect(screen.getByRole("heading", { name: "故事想法" })).toHaveAttribute("id", "heading-one-" + headingsForMarkdown(markdown)[0].anchor);
    await userEvent.setup().click(screen.getByRole("button", { name: "编辑 Markdown" }));
    expect(screen.getByRole("textbox")).toHaveValue(markdown);
    expect(manager.get("one")!.status).toBe("saved");
    manager.dispose();
  });
  it("renders Chinese field labels next to body text in nested lists while preserving escaped markers", () => {
    const markdown = "- **类型与作用：**推理主角\n  - **起点：**寻找妹妹\n\n\\*\\*示例：\\*\\*原样显示\n\n`**代码：**原样显示`";
    const manager = new DraftManager(vi.fn());
    manager.load(document("r1", markdown));
    const { container } = render(<DocumentEditor project="demo" draft={manager.get("one")!} manager={manager} onSelection={() => {}} />);
    expect(screen.getByText("类型与作用：").tagName).toBe("STRONG");
    expect(screen.getByText("起点：").tagName).toBe("STRONG");
    expect(screen.getByText("**示例：**原样显示")).toBeInTheDocument();
    expect(container.querySelector("code")).toHaveTextContent("**代码：**原样显示");
    manager.dispose();
  });

  it("previews readable document structure without exposing settings or changing the source", async () => {
    const user = userEvent.setup();
    const markdown = '<!-- nuomi-script-settings\n{"idea":"内部配置"}\n-->\n\n# 人物小传\n\n## 岑砚\n\n**类型与作用： **推理主角\n**性格： **克制\n\n- 寻找妹妹\n- 核验线索\n\n> 不轻信答案\n\n| 人物 | 目标 |\n| --- | --- |\n| 岑砚 | 寻人 |\n\n```text\n**原样代码： **不格式化\n```';
    const manager = new DraftManager(vi.fn());
    manager.load(document("r1", markdown));
    const { container } = render(<DocumentEditor project="demo" draft={manager.get("one")!} manager={manager} onSelection={() => {}} />);
    expect(container).not.toHaveTextContent("nuomi-script-settings");
    expect(container).not.toHaveTextContent("内部配置");
    expect(screen.getByText("类型与作用：").tagName).toBe("STRONG");
    expect(screen.getByText("性格：").tagName).toBe("STRONG");
    expect(container.querySelector("br")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(container.querySelector("blockquote")).toHaveTextContent("不轻信答案");
    expect(container.querySelector("pre code")).toHaveTextContent("**原样代码： **不格式化");
    const anchor = headingsForMarkdown(markdown)[0].anchor;
    expect(screen.getByRole("heading", { name: "岑砚" })).toHaveAttribute("id", "heading-one-" + anchor);
    await user.click(screen.getByRole("button", { name: "编辑 Markdown" }));
    expect(screen.getByRole("textbox", { name: "文档 Markdown" })).toHaveValue(markdown);
    expect(manager.get("one")!.status).toBe("saved");
    manager.dispose();
  });

  it("opens and selects exact codepoint evidence on navigation", async () => {
    const manager = new DraftManager(vi.fn());
    manager.load(document("r1", "甲😀证据乙"));
    render(<DocumentEditor project="demo" draft={manager.get("one")!} manager={manager} onSelection={() => {}}
      focusEvidence={{ document_id: "one", revision_id: "r1", block_id: "block", start: 2, end: 4, quote: "证据" }} />);
    const input = await screen.findByRole("textbox", { name: "文档 Markdown" }) as HTMLTextAreaElement;
    await waitFor(() => expect(input.value.slice(input.selectionStart, input.selectionEnd)).toBe("证据"));
    manager.dispose();
  });

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
    await waitFor(() => expect(save).toHaveBeenLastCalledWith("one", "r2", "my draft", expect.any(String)));
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
