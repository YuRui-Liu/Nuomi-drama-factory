import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GroupReferenceDialog } from "@/components/episode/narrative-workbench/group-reference-dialog";
import type { NarrativeGroupReferencePreview } from "@/lib/queries/narrative-groups";

const preview: NarrativeGroupReferencePreview = {
  style: { id: "style-1", label: "水墨电影感", prompt: "ink", enabled_by_default: true },
  character_references: [
    {
      id: "char-1", kind: "character", source_kind: "identity", label: "苏清晏（少女）",
      thumbnail_url: "/char-1.png", beat_numbers: [1, 3], enabled_by_default: true,
      character_name: "苏清晏", identity_id: "young", warning: null,
    },
    {
      id: "char-2", kind: "character", source_kind: "portrait_fallback", label: "沈砚",
      thumbnail_url: null, beat_numbers: [2], enabled_by_default: true,
      warning: "身份图缺失，已回退角色肖像",
    },
  ],
  scene_references: [
    {
      id: "scene-1", kind: "scene", source_kind: "scene_master", label: "雨夜长街",
      thumbnail_url: "/scene-1.png", beat_numbers: [1, 2], enabled_by_default: false,
      scene_id: "street", warning: null,
    },
  ],
  limits: { max_images: 2, selected_images: 2, omitted_reference_ids: ["scene-1"] },
  warnings: ["最多使用 2 张参考图，超限项目将省略"],
};

function renderDialog(overrides: Partial<React.ComponentProps<typeof GroupReferenceDialog>> = {}) {
  const onSubmit = vi.fn();
  const onOpenChange = vi.fn();
  const props = {
    open: true,
    preview,
    loading: false,
    error: null,
    onSubmit,
    onOpenChange,
    ...overrides,
  };
  return { ...render(<GroupReferenceDialog {...props} />), onSubmit, onOpenChange, props };
}

describe("GroupReferenceDialog", () => {
  it("selects backend defaults and shows fallback, missing-image, beat, and limit details", () => {
    renderDialog();
    expect(screen.getByRole("dialog", { name: "生成前引用确认" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "使用 2 张参考图生成" })).toBeInTheDocument();
    expect(screen.getByText("身份图")).toBeInTheDocument();
    expect(screen.getByText("肖像回退")).toBeInTheDocument();
    expect(screen.getByText("覆盖 beats 1、3")).toBeInTheDocument();
    expect(screen.getByText("缺少预览图")).toBeInTheDocument();
    expect(screen.getByText("身份图缺失，已回退角色肖像")).toBeInTheDocument();
    expect(screen.getByText("最多使用 2 张参考图，超限项目将省略")).toBeInTheDocument();
  });

  it("supports category switches and individual deselection", () => {
    renderDialog();
    fireEvent.click(screen.getByRole("checkbox", { name: "角色参考" }));
    expect(screen.getByRole("button", { name: "使用 0 张参考图生成" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "角色参考" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "取消引用 苏清晏（少女）" }));
    expect(screen.getByRole("button", { name: "使用 1 张参考图生成" })).toBeInTheDocument();
  });

  it("submits only the current local selection", () => {
    const { onSubmit } = renderDialog();
    fireEvent.click(screen.getByRole("checkbox", { name: "使用风格 水墨电影感" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "取消引用 沈砚" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "场景参考" }));
    fireEvent.click(screen.getByRole("button", { name: "使用 2 张参考图生成" }));
    expect(onSubmit).toHaveBeenCalledWith({
      useStyle: false,
      selectedCharacterReferenceIds: ["char-1"],
      selectedSceneReferenceIds: ["scene-1"],
    });
  });

  it("prevents submitting more references than the backend limit", () => {
    renderDialog();
    fireEvent.click(screen.getByRole("checkbox", { name: "场景参考" }));
    expect(screen.getByRole("button", { name: "使用 3 张参考图生成" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("已选择 3 张，超过最多 2 张限制");
  });

  it("offers retry when preview loading fails", () => {
    const onRetry = vi.fn();
    const { onSubmit } = renderDialog({ preview: null, error: new Error("预览失败"), onRetry });
    const submit = screen.getByRole("button", { name: "使用 0 张参考图生成" });
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("does not submit while the preview is loading", () => {
    const { onSubmit } = renderDialog({ preview: null, loading: true });
    const submit = screen.getByRole("button", { name: "使用 0 张参考图生成" });
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("restores backend defaults after closing and reopening", () => {
    const { rerender, props } = renderDialog();
    fireEvent.click(screen.getByRole("checkbox", { name: "取消引用 苏清晏（少女）" }));
    expect(screen.getByRole("button", { name: "使用 1 张参考图生成" })).toBeInTheDocument();
    rerender(<GroupReferenceDialog {...props} open={false} />);
    rerender(<GroupReferenceDialog {...props} open />);
    expect(screen.getByRole("button", { name: "使用 2 张参考图生成" })).toBeInTheDocument();
  });

  it("restores the new backend defaults when the preview changes while open", () => {
    const { rerender, onSubmit, props } = renderDialog();
    fireEvent.click(screen.getByRole("checkbox", { name: "取消引用 苏清晏（少女）" }));
    expect(screen.getByRole("button", { name: "使用 1 张参考图生成" })).toBeInTheDocument();

    const nextPreview: NarrativeGroupReferencePreview = {
      ...preview,
      style: { ...preview.style, id: "style-2", enabled_by_default: false },
      character_references: [
        {
          ...preview.character_references[0],
          id: "char-3",
          label: "苏清晏（成年）",
          enabled_by_default: false,
        },
        {
          ...preview.character_references[1],
          id: "char-4",
          enabled_by_default: true,
        },
      ],
      scene_references: [
        { ...preview.scene_references[0], id: "scene-2", enabled_by_default: true },
      ],
    };
    rerender(<GroupReferenceDialog {...props} preview={nextPreview} />);

    expect(screen.getByRole("button", { name: "使用 2 张参考图生成" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "使用风格 水墨电影感" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "添加引用 苏清晏（成年）" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "取消引用 沈砚" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "取消引用 雨夜长街" })).toBeChecked();

    fireEvent.click(screen.getByRole("button", { name: "使用 2 张参考图生成" }));
    expect(onSubmit).toHaveBeenCalledWith({
      useStyle: false,
      selectedCharacterReferenceIds: ["char-4"],
      selectedSceneReferenceIds: ["scene-2"],
    });
  });

  it("preserves local choices across equivalent preview refetches", () => {
    const { rerender, props } = renderDialog();
    fireEvent.click(screen.getByRole("checkbox", { name: "取消引用 苏清晏（少女）" }));
    expect(screen.getByRole("button", { name: "使用 1 张参考图生成" })).toBeInTheDocument();

    rerender(<GroupReferenceDialog {...props} preview={structuredClone(preview)} />);

    expect(screen.getByRole("checkbox", { name: "添加引用 苏清晏（少女）" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "使用 1 张参考图生成" })).toBeInTheDocument();
  });
});
