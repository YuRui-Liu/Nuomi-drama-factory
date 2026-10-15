import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { GroupReferenceDialog } from "@/components/episode/narrative-workbench/group-reference-dialog";
import type { PlannedNarrativeGroupReferencePreview } from "@/lib/queries/narrative-groups";
vi.mock("@/components/episode/narrative-workbench/character-reference-repair-dialog", () => ({ CharacterReferenceRepairDialog: ({ onBound }: { onBound: () => Promise<void> }) => <button onClick={() => void onBound()}>模拟绑定完成</button> }));
it.each([true, false])("preserves only an actual repair-owned revision, changed=%s", async (changed) => {
  const binding = { asset_kind: "character_identity" as const, beat_ids: [], required: false, resolution: "auto_matched" as const, selected_by_default: true };
  const preview: PlannedNarrativeGroupReferencePreview = { source_plan_revision_id: "plan", reference_revision: "r1", max_images: 4, bindings: [
    { ...binding, binding_id: "a", display_label: "已有甲", status: "ready", version_id: "v1" },
    { ...binding, binding_id: "b", display_label: "已有乙", status: "ready", version_id: "v2" },
    { ...binding, binding_id: "c", display_label: "新角色", status: "missing_asset" },
  ] };
  const onSubmit = vi.fn(); const onRetry = vi.fn().mockResolvedValue({ data: { data: { reference_revision: changed ? "r2" : "r1" } } });
  const props = { open: true, project: "p", episode: 1, groupId: "g", preview, onOpenChange: vi.fn(), onSubmit, onRetry, onUploadReference: vi.fn().mockResolvedValue({ upload_id: "u", url: "/u" }) };
  const { rerender } = render(<GroupReferenceDialog {...props} />);
  fireEvent.click(screen.getByRole("button", { name: /已有乙/ }));
  fireEvent.change(screen.getByLabelText("上传临时参考图"), { target: { files: [new File(["img"], "keep.png", { type: "image/png" })] } });
  await screen.findByText("keep.png");
  fireEvent.click(screen.getByRole("button", { name: "生成角色形象" }));
  fireEvent.click(screen.getByText("模拟绑定完成"));
  await waitFor(() => expect(onRetry).toHaveBeenCalledOnce());
  rerender(<GroupReferenceDialog {...props} preview={{ ...preview, reference_revision: "r2", bindings: preview.bindings.map((b) => b.binding_id === "c" ? { ...b, status: "ready", version_id: "v3" } : b) }} />);
  fireEvent.click(screen.getByRole("button", { name: changed ? "使用 2 张参考图生成" : "使用 3 张参考图生成" }));
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ selectedBindingIds: changed ? ["a"] : ["a", "b", "c"], uploadIds: changed ? ["u"] : [] }));
  rerender(<GroupReferenceDialog {...props} preview={{ ...preview, reference_revision: "r3" }} />);
  expect(screen.queryByText("keep.png")).not.toBeInTheDocument();
});
