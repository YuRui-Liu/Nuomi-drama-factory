import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ScriptSetter } from "./script-setter";
import { defaultSettings } from "./settings";

describe("ScriptSetter", () => {
  it("does not commit a Chinese IME confirmation and never toggles off duplicate custom tags", async () => {
    const user = userEvent.setup();
    render(<ScriptSetter initial={defaultSettings()} onSave={() => {}} onClose={() => {}} />);
    await user.click(screen.getByRole("button", { name: "编辑时代背景" }));
    const input = screen.getByRole("textbox", { name: "自定义时代背景" });
    fireEvent.change(input, { target: { value: "宋代" } });
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter", isComposing: true, keyCode: 229 });
    expect(screen.queryByRole("button", { name: "移除宋代" })).not.toBeInTheDocument();
    expect(input).toHaveValue("宋代");
    fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });
    expect(screen.queryByRole("button", { name: "移除宋代" })).not.toBeInTheDocument();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByRole("button", { name: "移除宋代" })).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "宋代" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByRole("button", { name: "移除宋代" })).toBeInTheDocument();
  });

  it("adds a custom era with no matches, deselects it, and saves single mode", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(<ScriptSetter initial={defaultSettings()} onSave={onSave} onClose={() => {}} />);
    await user.click(screen.getByRole("button", { name: "编辑时代背景" }));
    await user.type(screen.getByRole("searchbox", { name: "搜索时代背景" }), "自定义极寒世纪");
    await user.click(screen.getByRole("button", { name: "添加自定义时代背景" }));
    expect(screen.getByRole("button", { name: "移除自定义极寒世纪" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "移除自定义极寒世纪" }));
    expect(screen.queryByRole("button", { name: "移除自定义极寒世纪" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "编辑剧本结构" }));
    await user.click(screen.getByRole("button", { name: "单集 / 短片" }));
    expect(screen.queryByLabelText("计划集数")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "保存创作设定" }));
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ mode: "single", era: [] }));
  });

  it("shows preset choices as editable starting points", async () => {
    const user = userEvent.setup();
    render(<ScriptSetter initial={defaultSettings()} onSave={() => {}} onClose={() => {}} />);
    const presets = screen.getByLabelText("创作预设");
    await user.click(within(presets).getByRole("button", { name: /现实职场成长/ }));
    expect(screen.getAllByRole("button", { name: "都市现实" })[0]).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("预设只是创作起点，可继续调整。")).toBeInTheDocument();
  });
});
