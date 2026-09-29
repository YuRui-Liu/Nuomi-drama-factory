import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ScriptSetter } from "./script-setter";
import { defaultSettings } from "./settings";

const open = async (name: string) => {
  await userEvent.click(screen.getByRole("button", { name: "编辑" + name }));
  return screen.getByRole("dialog", { name: "编辑" + name });
};
const done = async () => userEvent.click(screen.getByRole("button", { name: "完成设置" }));

describe("ScriptSetter", () => {
  it("uses one search/custom input, ignores IME confirmation and keeps duplicate choices selected", async () => {
    render(<ScriptSetter initial={defaultSettings()} onSave={() => {}} onClose={() => {}} />);
    const drawer = await open("时代背景");
    const input = within(drawer).getByRole("searchbox", { name: "搜索或自定义时代背景" });
    expect(within(drawer).queryAllByRole("textbox")).toHaveLength(0);
    fireEvent.change(input, { target: { value: "宋代" } });
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter", isComposing: true, keyCode: 229 });
    expect(screen.queryByRole("button", { name: "移除宋代" })).not.toBeInTheDocument();
    fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });
    expect(screen.queryByRole("button", { name: "移除宋代" })).not.toBeInTheDocument();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByRole("button", { name: "移除宋代" })).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "宋代" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getAllByRole("button", { name: "移除宋代" })).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "移除宋代" }));
    expect(screen.queryByRole("button", { name: "移除宋代" })).not.toBeInTheDocument();
  });

  it("has eight presets and undo preserves later manual edits and non-preset settings", async () => {
    const initial = { ...defaultSettings(), idea: "原始想法", style: ["水墨动画"], structure: ["人物驱动"], episodeCount: 8, durationSeconds: 126 };
    const onSave = vi.fn();
    render(<ScriptSetter initial={initial} onSave={onSave} onClose={() => {}} />);
    const presets = screen.getByLabelText("创作预设");
    expect(within(presets).getAllByRole("button")).toHaveLength(8);
    const preset = within(presets).getByRole("button", { name: /玄幻修仙逆袭/ });
    await userEvent.click(preset);
    expect(preset).toHaveAttribute("aria-pressed", "true");
    fireEvent.change(screen.getByRole("textbox", { name: "故事想法" }), { target: { value: "后来补充的想法" } });
    await userEvent.click(screen.getAllByRole("button", { name: "悬疑推理" })[0]);
    await userEvent.click(screen.getByRole("button", { name: "撤销预设" }));
    await userEvent.click(screen.getByRole("button", { name: "保存创作设定" }));
    expect(onSave).toHaveBeenCalledWith({ ...initial, genrePrimary: "悬疑推理", idea: "后来补充的想法" });
    expect(initial.idea).toBe("原始想法");
  });

  it("closes the focused drawer before the main dialog on Escape", async () => {
    const onClose = vi.fn();
    render(<ScriptSetter initial={defaultSettings()} onSave={() => {}} onClose={onClose} />);
    await open("核心看点");
    await waitFor(() => expect(screen.getByRole("searchbox")).toHaveFocus());
    await userEvent.tab({ shift: true });
    expect(screen.getByRole("button", { name: "关闭核心看点" })).toHaveFocus();
    await userEvent.tab({ shift: true });
    await waitFor(() => expect(screen.getByRole("button", { name: "完成设置" })).toHaveFocus());
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "编辑核心看点" })).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole("button", { name: "编辑核心看点" })).toHaveFocus());
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("cancels without saving or mutating the initial settings", async () => {
    const initial = defaultSettings();
    const onSave = vi.fn(), onClose = vi.fn();
    render(<ScriptSetter initial={initial} onSave={onSave} onClose={onClose} />);
    await open("角色设定");
    await userEvent.click(screen.getByRole("button", { name: "普通打工人" }));
    await done();
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onSave).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(initial.roles).toEqual([]);
  });

  it("normalizes single and series episode counts and whole-second duration", async () => {
    const onSave = vi.fn();
    render(<ScriptSetter initial={defaultSettings()} onSave={onSave} onClose={() => {}} />);
    await open("剧本结构");
    fireEvent.change(screen.getByLabelText("计划集数"), { target: { value: "201" } });
    fireEvent.change(screen.getByLabelText("目标时长 / 秒"), { target: { value: "15.6" } });
    await done();
    await userEvent.click(screen.getByRole("button", { name: "保存创作设定" }));
    expect(onSave).toHaveBeenLastCalledWith(expect.objectContaining({ episodeCount: 100, durationSeconds: 16 }));
    await open("剧本结构");
    await userEvent.click(screen.getByRole("button", { name: "单集 / 短片" }));
    expect(screen.queryByLabelText("计划集数")).not.toBeInTheDocument();
    await done();
    await userEvent.click(screen.getByRole("button", { name: "保存创作设定" }));
    expect(onSave).toHaveBeenLastCalledWith(expect.objectContaining({ mode: "single", episodeCount: 1 }));
    await open("剧本结构");
    await userEvent.click(screen.getByRole("button", { name: "连续短剧" }));
    expect(screen.getByLabelText("计划集数")).toHaveValue(2);
  });

  it("retains the draft after save failure and prevents duplicate saves while pending", async () => {
    let reject!: (error: Error) => void;
    const onSave = vi.fn().mockImplementationOnce(() => new Promise<void>((_, no) => { reject = no; })).mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(<ScriptSetter initial={defaultSettings()} onSave={onSave} onClose={onClose} />);
    fireEvent.change(screen.getByRole("textbox", { name: "故事想法" }), { target: { value: "保留这段创意" } });
    const save = screen.getByRole("button", { name: "保存创作设定" });
    fireEvent.click(save); fireEvent.click(save);
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(save).toBeDisabled();
    await act(async () => reject(new Error("网络中断")));
    expect(screen.getByRole("alert")).toHaveTextContent("网络中断");
    expect(screen.getByRole("textbox", { name: "故事想法" })).toHaveValue("保留这段创意");
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.click(save);
    expect(onSave).toHaveBeenCalledTimes(2);
    expect(onSave).toHaveBeenLastCalledWith(expect.objectContaining({ idea: "保留这段创意" }));
  });
});
