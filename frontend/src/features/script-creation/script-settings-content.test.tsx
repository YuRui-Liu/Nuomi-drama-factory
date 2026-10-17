import { useState } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { ScriptSettingsContent } from "./script-settings-content";
import { defaultSettings } from "./settings";
import type { ScriptSettings } from "./types";

function ControlledSettings({ initial = defaultSettings(), onChange = vi.fn() }: {
  initial?: ScriptSettings;
  onChange?: (value: ScriptSettings) => void;
}) {
  const [value, setValue] = useState(initial);
  return <ScriptSettingsContent value={value} onChange={next => { onChange(next); setValue(next); }} />;
}

const open = async (title: string) => {
  const trigger = screen.getByRole("button", { name: "编辑" + title });
  await userEvent.click(trigger);
  return { trigger, drawer: screen.getByRole("dialog", { name: "编辑" + title }) };
};

describe("ScriptSettingsContent", () => {
  it("returns focus to the drawer trigger when nested in an outer dialog", async () => {
    function NestedDialog() {
      const [value, setValue] = useState(defaultSettings());
      return <Dialog open>
        <DialogContent aria-label="故事创作">
          <DialogTitle>故事创作</DialogTitle>
          <ScriptSettingsContent value={value} onChange={setValue} />
        </DialogContent>
      </Dialog>;
    }

    render(<NestedDialog />);
    const trigger = screen.getByRole("button", { name: "编辑角色设定" });
    await userEvent.click(trigger);
    const drawer = screen.getByRole("dialog", { name: "编辑角色设定" });
    await userEvent.click(within(drawer).getByRole("button", { name: "取消编辑" }));

    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("places the story idea after genre fusion and before the six setting summaries", () => {
    render(<ControlledSettings />);
    const fusion = screen.getByText("围绕单一题材展开");
    const idea = screen.getByRole("textbox", { name: "故事想法" });
    const firstSummary = screen.getByRole("button", { name: "编辑目标受众" });

    expect(fusion.compareDocumentPosition(idea) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(idea.compareDocumentPosition(firstSummary) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("keeps drawer edits temporary until Apply commits the controlled value", async () => {
    const onChange = vi.fn();
    render(<ControlledSettings onChange={onChange} />);
    const { drawer } = await open("角色设定");

    await userEvent.click(within(drawer).getByRole("button", { name: "普通打工人" }));

    expect(onChange).not.toHaveBeenCalled();

    await userEvent.click(within(drawer).getByRole("button", { name: "完成设置" }));

    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ roles: ["普通打工人"] }));
    expect(within(screen.getByRole("button", { name: "编辑角色设定" })).getByText("普通打工人")).toBeInTheDocument();
  });

  it("discards drawer edits on Cancel and Escape, then restores focus to the trigger", async () => {
    const onChange = vi.fn();
    render(<ControlledSettings onChange={onChange} />);
    const { drawer, trigger } = await open("核心看点");

    await userEvent.click(within(drawer).getByRole("button", { name: "职业成长" }));
    await userEvent.click(within(drawer).getByRole("button", { name: "取消编辑" }));

    expect(onChange).not.toHaveBeenCalled();
    expect(within(trigger).getByText("暂未指定")).toBeInTheDocument();

    const reopened = await open("核心看点");
    await userEvent.click(within(reopened.drawer).getByRole("button", { name: "职业成长" }));
    await userEvent.keyboard("{Escape}");

    expect(onChange).not.toHaveBeenCalled();
    expect(within(reopened.trigger).getByText("暂未指定")).toBeInTheDocument();
    await waitFor(() => expect(reopened.trigger).toHaveFocus());
  });

  it("merges an applied drawer category into the latest controlled value", async () => {
    const onChange = vi.fn();
    const initial = defaultSettings();
    const view = render(<ScriptSettingsContent value={initial} onChange={onChange} />);
    const { drawer } = await open("角色设定");
    await userEvent.click(within(drawer).getByRole("button", { name: "普通打工人" }));

    const latest = { ...initial, idea: "父级刚更新的想法" };
    view.rerender(<ScriptSettingsContent value={latest} onChange={onChange} />);
    await userEvent.click(within(screen.getByRole("dialog", { name: "编辑角色设定" })).getByRole("button", { name: "完成设置" }));

    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ roles: ["普通打工人"], idea: "父级刚更新的想法" }));
  });

  it("preserves the visible story idea when applying and undoing a preset", async () => {
    const onChange = vi.fn();
    render(<ControlledSettings onChange={onChange} />);

    const idea = screen.getByRole("textbox", { name: "故事想法" });
    await userEvent.type(idea, "主角醒来后发现全城失忆");
    await userEvent.click(screen.getByRole("button", { name: /玄幻修仙逆袭/ }));

    expect(screen.getByRole("textbox", { name: "故事想法" })).toHaveValue("主角醒来后发现全城失忆");
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ genrePrimary: "玄幻修仙", idea: "主角醒来后发现全城失忆" }));

    await userEvent.click(screen.getAllByRole("button", { name: "悬疑推理" })[0]);
    await userEvent.click(screen.getByRole("button", { name: "撤销预设" }));

    expect(screen.getByRole("textbox", { name: "故事想法" })).toHaveValue("主角醒来后发现全城失忆");
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ genrePrimary: "悬疑推理", idea: "主角醒来后发现全城失忆" }));
  });

  it("normalizes structure values when Apply commits, even without a preceding blur", async () => {
    const onChange = vi.fn();
    render(<ControlledSettings onChange={onChange} />);
    const { drawer } = await open("剧本结构");

    await userEvent.click(within(drawer).getByRole("button", { name: "人物驱动" }));
    fireEvent.change(within(drawer).getByLabelText("计划集数"), { target: { value: "201" } });
    fireEvent.change(within(drawer).getByLabelText("目标时长 / 秒"), { target: { value: "15.6" } });
    await userEvent.click(within(drawer).getByRole("button", { name: "完成设置" }));

    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ episodeCount: 100, durationSeconds: 16, structure: ["人物驱动"] }));
    const secondDrawer = await open("剧本结构");
    await userEvent.click(within(secondDrawer.drawer).getByRole("button", { name: "单集 / 短片" }));
    await userEvent.click(within(secondDrawer.drawer).getByRole("button", { name: "完成设置" }));

    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ mode: "single", episodeCount: 1, structure: ["人物驱动"] }));
  });
});
