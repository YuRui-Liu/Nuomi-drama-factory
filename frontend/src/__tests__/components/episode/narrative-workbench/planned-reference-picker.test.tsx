import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PlannedReferencePicker } from "@/components/episode/narrative-workbench/planned-reference-picker";
import type { PlannedReferenceBinding } from "@/lib/queries/narrative-groups";

const bindings: PlannedReferenceBinding[] = [
  {
    binding_id: "character-1", asset_kind: "character_identity", display_label: "苏清晏",
    beat_ids: ["beat-1"], required: true, status: "ready", selected_by_default: true,
    thumbnail_url: "/character.png",
  },
  {
    binding_id: "scene-variant-1", asset_kind: "scene_variant", display_label: "雨夜长街",
    variant_id: "rain", beat_ids: ["beat-1", "beat-2"], required: true,
    status: "ready", selected_by_default: true,
  },
  {
    binding_id: "scene-base-1", asset_kind: "scene_base", display_label: "旧宅",
    beat_ids: ["beat-3"], required: false, status: "ready", selected_by_default: false,
  },
  {
    binding_id: "prop-1", asset_kind: "prop", display_label: "旧灯笼",
    beat_ids: ["beat-2"], required: false, status: "ready", selected_by_default: false,
  },
];

function ControlledPicker({
  initial = [], maxImages = 5, temporaryCount = 0, items = bindings,
  onResolvePlanning = vi.fn(), onChange,
}: {
  initial?: string[];
  maxImages?: number;
  temporaryCount?: number;
  items?: PlannedReferenceBinding[];
  onResolvePlanning?: () => void;
  onChange?: (ids: string[]) => void;
}) {
  const [selectedIds, setSelectedIds] = useState(initial);
  return <PlannedReferencePicker
    bindings={items}
    selectedIds={selectedIds}
    maxImages={maxImages}
    temporaryCount={temporaryCount}
    onChange={(ids) => { setSelectedIds(ids); onChange?.(ids); }}
    onResolvePlanning={onResolvePlanning}
  />;
}

describe("PlannedReferencePicker", () => {
  it("locks required ready bindings and uses optional whole cards as accessible toggles", async () => {
    const user = userEvent.setup();
    render(<ControlledPicker />);
    const required = screen.getByRole("button", { name: /苏清晏/ });
    const card = screen.getByRole("button", { name: /旧宅/ });

    expect(required).toBeDisabled();
    expect(required).toHaveAttribute("aria-pressed", "true");
    expect(within(required).getByText("必选", { selector: "span" })).toBeInTheDocument();

    expect(card).toHaveAttribute("type", "button");
    expect(card).toHaveAttribute("aria-pressed", "false");
    expect(card).toHaveAttribute("data-selection-state", "unselected");
    expect(card).toHaveClass("border-white/30", "bg-white/[0.06]");
    expect(within(card).getByText("未选择", { selector: "span" })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(within(card).queryByTestId("selected-check")).not.toBeInTheDocument();

    await user.click(card);
    expect(card).toHaveAttribute("aria-pressed", "true");
    expect(card).toHaveAttribute("data-selection-state", "selected");
    expect(card).toHaveClass("border-cyan-300", "bg-cyan-300/20");
    expect(within(card).getByText("已选择", { selector: "span" })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(within(card).getByTestId("selected-check")).toHaveAttribute("aria-hidden", "true");

    card.focus();
    await user.keyboard("{Enter}");
    expect(card).toHaveAttribute("aria-pressed", "false");
    await user.keyboard(" ");
    expect(card).toHaveAttribute("aria-pressed", "true");
  });

  it("groups references and applies stable category and global select/clear actions", () => {
    const onChange = vi.fn();
    render(<ControlledPicker maxImages={3} onChange={onChange} />);
    expect(screen.getByRole("heading", { name: "角色身份" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "场景与变体" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "道具" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "全选全部规划参考" }));
    expect(onChange).toHaveBeenLastCalledWith(["character-1", "scene-variant-1", "scene-base-1"]);
    expect(screen.getByText("已选 3 张 / 上限 3 张")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "清空场景与变体" }));
    expect(onChange).toHaveBeenLastCalledWith(["character-1", "scene-variant-1"]);
    fireEvent.click(screen.getByRole("button", { name: "全选道具" }));
    expect(onChange).toHaveBeenLastCalledWith(["character-1", "scene-variant-1", "prop-1"]);
    fireEvent.click(screen.getByRole("button", { name: "清空全部规划参考" }));
    expect(onChange).toHaveBeenLastCalledWith(["character-1", "scene-variant-1"]);
  });

  it("uses instance-unique heading relationships", () => {
    render(<><ControlledPicker /><ControlledPicker /></>);
    const headings = screen.getAllByRole("heading", { name: "角色身份" });
    expect(headings[0].id).not.toBe(headings[1].id);
    expect(headings[0].closest("section")).toHaveAttribute("aria-labelledby", headings[0].id);
    expect(headings[1].closest("section")).toHaveAttribute("aria-labelledby", headings[1].id);
  });

  it("renders only the first occurrence of a duplicate binding ID", () => {
    render(<ControlledPicker items={[
      bindings[0],
      { ...bindings[0], display_label: "不应出现的重复角色" },
      bindings[1],
    ]} />);
    expect(screen.getAllByRole("button", { name: /苏清晏/ })).toHaveLength(1);
    expect(screen.queryByText("不应出现的重复角色")).not.toBeInTheDocument();
  });

  it("counts temporary images and disables optional additions at the limit", () => {
    render(<ControlledPicker initial={["character-1", "scene-variant-1"]} maxImages={3} temporaryCount={1} />);
    expect(screen.getByText("已选 3 张 / 上限 3 张")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /雨夜长街/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /旧灯笼/ })).toBeDisabled();
  });

  it("reserves temporary image slots for both global and category select-all", () => {
    const onChange = vi.fn();
    render(<ControlledPicker maxImages={3} temporaryCount={1} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "全选全部规划参考" }));
    expect(onChange).toHaveBeenLastCalledWith(["character-1", "scene-variant-1"]);
    expect(screen.getByText("已选 3 张 / 上限 3 张")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "清空全部规划参考" }));
    fireEvent.click(screen.getByRole("button", { name: "全选场景与变体" }));
    expect(onChange).toHaveBeenLastCalledWith(["character-1", "scene-variant-1"]);
    expect(screen.getByText("已选 3 张 / 上限 3 张")).toBeInTheDocument();
  });

  it("ignores duplicate, unknown, and unavailable selected IDs when counting", () => {
    const unavailable: PlannedReferenceBinding = {
      binding_id: "missing-1", asset_kind: "prop", display_label: "失落印章",
      beat_ids: [], required: false, status: "missing_image", selected_by_default: false,
    };
    render(<ControlledPicker
      initial={["character-1", "character-1", "unknown", "missing-1"]}
      items={[...bindings, unavailable]}
    />);
    expect(screen.getByText("已选 2 张 / 上限 5 张")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /失落印章/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("explains unavailable references and exposes planning resolution for required items", () => {
    const onResolvePlanning = vi.fn();
    const unavailable: PlannedReferenceBinding[] = [
      {
        binding_id: "pending-1", asset_kind: "scene_variant", display_label: "大厅雨夜版",
        beat_ids: ["beat-1"], required: true, status: "pending_confirmation",
        selected_by_default: false, warning: "需要确认雨夜变体",
      },
      {
        binding_id: "missing-asset-1", asset_kind: "prop", display_label: "密信",
        beat_ids: ["beat-2"], required: true, status: "missing_asset", selected_by_default: false,
      },
      {
        binding_id: "missing-image-1", asset_kind: "character_identity", display_label: "沈砚",
        beat_ids: ["beat-3"], required: false, status: "missing_image", selected_by_default: false,
      },
    ];
    render(<ControlledPicker items={unavailable} onResolvePlanning={onResolvePlanning} />);

    expect(screen.getByText("需要确认雨夜变体")).toBeInTheDocument();
    expect(screen.getByText("缺少项目资产")).toBeInTheDocument();
    expect(screen.getByText("缺少资产图片")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /大厅雨夜版/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /密信/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /沈砚/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "返回规划处理不可用引用" }));
    expect(onResolvePlanning).toHaveBeenCalledOnce();
  });
});
