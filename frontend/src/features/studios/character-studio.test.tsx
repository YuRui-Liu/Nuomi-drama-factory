import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CharacterStudio } from "./character-studio";
import { useCharacterDraft } from "./character-draft-bridge";
const fixtures = vi.hoisted(() => ({ characters: [] as Array<{ name: string; portrait_url?: string; description?: string }> }));
vi.mock("@/lib/queries/characters", () => ({ useCharacters: () => ({ data: { data: fixtures.characters }, isPending: false }) }));
vi.mock("@/components/assets/character-casting-panel", () => ({ CharacterCastingPanel: () => <div>选角编辑器</div> }));
vi.mock("./character-library", () => ({ CharacterLibrary: () => <div>角色卡库</div> }));
vi.mock("./character-impact", () => ({ CharacterImpact: () => <div>真实影响检查</div> }));
vi.mock("./character-costume", () => ({ CharacterCostume: () => <div>服装编辑器</div> }));
describe("CharacterStudio", () => {
  beforeEach(() => { fixtures.characters = []; });
  it("bounds rail descriptions while preserving full selected details and full-text search", () => {
    const description = "人物详细设定\n".repeat(400) + "末尾关键词";
    fixtures.characters = [{ name: "长描述角色", description }, { name: "其他角色" }];
    render(<CharacterStudio project="long-description" />);
    const card = screen.getByRole("button", { name: "选择角色 长描述角色" });
    expect(card.textContent!.length).toBeLessThan(90);
    expect(card).toHaveTextContent("…");
    expect(card).not.toHaveTextContent("末尾关键词");
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "末尾关键词" } });
    expect(screen.getByRole("button", { name: "选择角色 长描述角色" })).toBeInTheDocument();
    fireEvent.click(card);
    const details = screen.getByText("查看 长描述角色 完整描述").closest("details")!;
    expect(details.textContent).toContain(description);
    expect(details).not.toHaveAttribute("open");
  });
  it("does not fabricate characters or select a missing object", () => {
    render(<CharacterStudio project="empty" />);
    expect(screen.getByText(/项目中还没有角色/)).toBeInTheDocument();
    expect(screen.queryByText("选角编辑器")).not.toBeInTheDocument();
  });
  it("filters real portrait cards and switches to the selected character's costume workspace", () => {
    fixtures.characters = [{ name: "项目甲", portrait_url: "/actual.png", description: "实际人物" }, { name: "项目乙" }];
    render(<CharacterStudio project="real" />);
    expect(screen.getByRole("img", { name: "项目甲当前形象" })).toHaveAttribute("src", "/actual.png");
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索项目角色" }), { target: { value: "项目甲" } });
    expect(screen.queryByRole("button", { name: /选择角色 项目乙/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /选择角色 项目甲/ }));
    expect(screen.getByRole("button", { name: /选择角色 项目甲/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("选角编辑器")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "服装造型" }));
    expect(screen.getByText("服装编辑器")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索项目角色" }), { target: { value: "无匹配" } });
    expect(screen.getByText("没有匹配的角色，请调整搜索词。")).toBeInTheDocument();
    expect(screen.getByText("服装编辑器")).toBeInTheDocument();
  });
  it("keeps card selection unchanged until unsaved edits are explicitly discarded", () => {
    fixtures.characters = [{ name: "项目甲" }, { name: "项目乙" }];
    function Draft() { useCharacterDraft("test-card-selection", true, vi.fn()); return null; }
    const view = render(<><Draft /><CharacterStudio project="guarded" /></>);
    fireEvent.click(screen.getByRole("button", { name: "选择角色 项目甲" }));
    expect(screen.getByRole("dialog", { name: "保存角色修改" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "选择角色 项目甲" })).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByRole("button", { name: "继续编辑" }));
    expect(screen.queryByText("选角编辑器")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "选择角色 项目乙" }));
    fireEvent.click(screen.getByRole("button", { name: "放弃修改" }));
    expect(screen.getByRole("button", { name: "选择角色 项目乙" })).toHaveAttribute("aria-pressed", "true");
    view.unmount();
  });
});
