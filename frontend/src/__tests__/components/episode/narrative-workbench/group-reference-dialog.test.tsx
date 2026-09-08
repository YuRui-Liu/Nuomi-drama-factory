import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GroupReferenceDialog } from "@/components/episode/narrative-workbench/group-reference-dialog";
import type { PlannedNarrativeGroupReferencePreview } from "@/lib/queries/narrative-groups";

const preview: PlannedNarrativeGroupReferencePreview = {
  reference_revision: "director-plan-r7",
  max_images: 3,
  bindings: [
    { binding_id: "identity:hero:young", asset_kind: "character_identity", display_label: "石九 / 青年时期", variant_id: "young", beat_ids: ["beat-1"], required: true, status: "ready", selected_by_default: true, thumbnail_url: "/hero.png", version_id: "hero-v1" },
    { binding_id: "scene:hall:rain", asset_kind: "scene_variant", display_label: "谢家碑坊 / 暴雨天井", variant_id: "rain", beat_ids: ["beat-1"], required: false, status: "ready", selected_by_default: true, thumbnail_url: "/hall.png", version_id: "hall-rain-v1" },
    { binding_id: "prop:tablet", asset_kind: "prop", display_label: "深灰功德碑", beat_ids: ["beat-1"], required: false, status: "missing_image", selected_by_default: false, warning: "请先在规划阶段补齐道具参考图" },
  ],
};

function renderDialog(overrides: Partial<React.ComponentProps<typeof GroupReferenceDialog>> = {}) {
  const onSubmit = vi.fn();
  const onOpenChange = vi.fn();
  const onResolvePlanning = vi.fn();
  const onUploadReference = vi.fn().mockResolvedValue({ upload_id: "upload-1", mime_type: "image/png", size_bytes: 10, temporary: true, persisted: false, persistence_warning: "", url: "/temporary/upload-1" });
  const props = { open: true, preview, loading: false, error: null, onSubmit, onOpenChange, onResolvePlanning, onUploadReference, ...overrides };
  return { ...render(<GroupReferenceDialog {...props} />), props, onSubmit, onOpenChange, onResolvePlanning, onUploadReference };
}

describe("GroupReferenceDialog planned references", () => {
  it("initializes ready defaults, groups bindings, and submits stable IDs", () => {
    const { onSubmit } = renderDialog();
    expect(screen.getByRole("button", { name: /石九 \/ 青年时期/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /谢家碑坊 \/ 暴雨天井/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /深灰功德碑/ })).toBeDisabled();
    expect(screen.getAllByText("已选择")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "使用 2 张参考图生成" }));
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ selectedBindingIds: ["identity:hero:young", "scene:hall:rain"], uploadIds: [], referenceRevision: "director-plan-r7", useStyle: true }));
  });

  it("keeps required bindings selected while optional whole cards support cancellation", () => {
    renderDialog();
    const hero = screen.getByRole("button", { name: /石九 \/ 青年时期/ });
    fireEvent.click(hero);
    expect(hero).toHaveAttribute("aria-pressed", "true");
    expect(hero).toBeDisabled();
    expect(screen.getByRole("button", { name: "使用 2 张参考图生成" })).toBeEnabled();
  });

  it("keeps style independent from the image count", () => {
    const { onSubmit } = renderDialog();
    fireEvent.click(screen.getByRole("checkbox", { name: "使用项目风格" }));
    fireEvent.click(screen.getByRole("button", { name: "使用 2 张参考图生成" }));
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ useStyle: false }));
  });

  it("uploads temporary images and counts them against the limit", async () => {
    const { onSubmit, onUploadReference } = renderDialog();
    const file = new File(["image"], "临时构图.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("上传临时参考图"), { target: { files: [file] } });
    await waitFor(() => expect(onUploadReference).toHaveBeenCalledWith(file));
    expect(screen.getByText("临时构图.png")).toBeInTheDocument();
    expect(screen.getByText("仅本次生成")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "使用 3 张参考图生成" })).toBeEnabled();
    expect(screen.getByLabelText("上传临时参考图")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "使用 3 张参考图生成" }));
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ uploadIds: ["upload-1"] }));
  });

  it("shows a retryable inline error when a temporary upload fails", async () => {
    const onUploadReference = vi.fn().mockRejectedValue(new Error("文件格式不支持"));
    const onResetUploadError = vi.fn();
    renderDialog({ onUploadReference, onResetUploadError });
    const file = new File(["bad"], "坏图.gif", { type: "image/gif" });

    fireEvent.change(screen.getByLabelText("上传临时参考图"), { target: { files: [file] } });

    expect(await screen.findByRole("alert")).toHaveTextContent("文件格式不支持");
    expect(onResetUploadError).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "重新选择临时参考图" })).toBeEnabled();
  });

  it("returns unresolved bindings to planning instead of resolving them during generation", () => {
    const unresolved = {
      ...preview,
      bindings: [{ ...preview.bindings[0], status: "pending_confirmation" as const }],
    };
    const { onResolvePlanning } = renderDialog({ preview: unresolved });
    expect(screen.queryByText("待处理问题")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回规划处理不可用引用" }));
    expect(onResolvePlanning).toHaveBeenCalledOnce();
  });

  it("blocks submission when a required ready binding has no version", () => {
    const unresolved = {
      ...preview,
      bindings: [{ ...preview.bindings[0], version_id: "" }],
    };
    const { onSubmit, onResolvePlanning } = renderDialog({ preview: unresolved });

    expect(screen.getByRole("button", { name: "使用 0 张参考图生成" })).toBeDisabled();
    expect(screen.getByText("必需引用尚未就绪，请先返回规划处理。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回规划处理不可用引用" }));
    expect(onResolvePlanning).toHaveBeenCalledOnce();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("preserves choices across equivalent refetches and resets on a new revision", () => {
    const { rerender, props } = renderDialog();
    fireEvent.click(screen.getByRole("button", { name: /谢家碑坊 \/ 暴雨天井/ }));
    rerender(<GroupReferenceDialog {...props} preview={structuredClone(preview)} />);
    expect(screen.getByRole("button", { name: /谢家碑坊 \/ 暴雨天井/ })).toHaveAttribute("aria-pressed", "false");
    rerender(<GroupReferenceDialog {...props} preview={{ ...preview, reference_revision: "director-plan-r8" }} />);
    expect(screen.getByRole("button", { name: /谢家碑坊 \/ 暴雨天井/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("defaults unconstrained render generation on and restores it when reopened", () => {
    const { rerender, props } = renderDialog({ stage: "render", sketchReady: false });
    const checkbox = screen.getByRole("checkbox", { name: /允许无草图约束生成/ });
    expect(checkbox).toBeChecked();
    fireEvent.click(checkbox);
    expect(screen.getByRole("button", { name: "使用 2 张参考图生成" })).toBeDisabled();
    rerender(<GroupReferenceDialog {...props} open={false} />);
    rerender(<GroupReferenceDialog {...props} open />);
    expect(screen.getByRole("checkbox", { name: /允许无草图约束生成/ })).toBeChecked();
  });

  it("offers retry while keeping the dialog open when preview loading fails", () => {
    const onRetry = vi.fn();
    const { onOpenChange, onResolvePlanning } = renderDialog({ preview: null, error: new Error("引用已过期"), onRetry });
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "返回规划" }));
    expect(onResolvePlanning).toHaveBeenCalledOnce();
    expect(onOpenChange).not.toHaveBeenCalled();
  });
});
