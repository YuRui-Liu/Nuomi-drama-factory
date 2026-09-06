import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GroupVideoReferenceDialog } from "@/components/episode/narrative-workbench/group-video-reference-dialog";

const m = vi.hoisted(() => ({ upload: vi.fn(), save: vi.fn() }));
vi.mock("@/lib/queries/narrative-groups", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/queries/narrative-groups")>(),
  useUploadNarrativeGroupVideoReference: () => ({ mutateAsync: m.upload, isPending: false }),
  useUpdateNarrativeGroupVideoReferences: () => ({ mutateAsync: m.save, isPending: false }),
}));

const candidates = [
  { reference_id: "hero", source_kind: "character_identity" as const, label: "Hero", subject_description: "The hero", thumbnail_url: "/hero.png" },
  { reference_id: "room", source_kind: "scene_master" as const, label: "Room", subject_description: "The room", thumbnail_url: "/room.png" },
  { reference_id: "prop", source_kind: "prop_reference" as const, label: "Sword", subject_description: "The sword", thumbnail_url: "/prop.png" },
];
const preview = { revision: 4, max_images: 5, candidates, selected: [], warnings: [] };

describe("GroupVideoReferenceDialog", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("defaults an empty saved selection in candidate order without saving", () => {
    render(<GroupVideoReferenceDialog open onOpenChange={vi.fn()} project="p" episode={1} groupId="g" preview={preview} minImages={1} maxImages={2} />);
    expect(screen.getByText("Picture 1")).toBeInTheDocument();
    expect(screen.getByText(/Subject 2/)).toBeInTheDocument();
    expect(screen.getAllByRole("textbox")).toHaveLength(2);
    expect(m.save).not.toHaveBeenCalled();
  });

  it("restores saved order and descriptions, edits, reorders by keyboard and drag, then saves CAS body", async () => {
    const user = userEvent.setup();
    m.save.mockResolvedValue({ ok: true, data: { ...preview, revision: 5, selected: [] } });
    const onSaved = vi.fn();
    render(<GroupVideoReferenceDialog open onOpenChange={vi.fn()} project="p" episode={1} groupId="g" preview={{ ...preview, selected: [
      { reference_id: "room", subject_description: "Saved room" },
      { reference_id: "hero", subject_description: "Saved hero" },
    ] }} minImages={1} maxImages={5} onSaved={onSaved} />);
    expect(screen.getAllByRole("textbox").map((node) => (node as HTMLInputElement).value)).toEqual(["Saved room", "Saved hero"]);
    await user.clear(screen.getAllByRole("textbox")[0]);
    await user.type(screen.getAllByRole("textbox")[0], "Updated room");
    await user.click(screen.getByRole("button", { name: "下移 Room" }));
    fireEvent.dragStart(screen.getByTestId("selected-hero"));
    fireEvent.dragOver(screen.getByTestId("selected-room"));
    fireEvent.drop(screen.getByTestId("selected-room"));
    await user.click(screen.getByRole("button", { name: "保存参考图" }));
    expect(m.save).toHaveBeenCalledWith({ groupId: "g", expectedRevision: 4, references: [
      { reference_id: "hero", subject_description: "Saved hero" },
      { reference_id: "room", subject_description: "Updated room" },
    ] });
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ revision: 5 })));
  });

  it("validates count, duplicate ids, and trimmed single-line descriptions", async () => {
    const { rerender } = render(<GroupVideoReferenceDialog open onOpenChange={vi.fn()} project="p" episode={1} groupId="g" preview={{ ...preview, candidates: [], selected: [] }} minImages={1} maxImages={5} />);
    expect(screen.getByText(/至少选择 1 张/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存参考图" })).toBeDisabled();
    rerender(<GroupVideoReferenceDialog open onOpenChange={vi.fn()} project="p" episode={1} groupId="g" preview={{ ...preview, revision: 5, selected: [{ reference_id: "hero", subject_description: "ok" }, { reference_id: "hero", subject_description: "ok" }] }} minImages={1} maxImages={5} />);
    expect(screen.getByText(/不能重复/)).toBeInTheDocument();
    rerender(<GroupVideoReferenceDialog open onOpenChange={vi.fn()} project="p" episode={1} groupId="g" preview={{ ...preview, revision: 6, selected: [{ reference_id: "hero", subject_description: "ok" }, { reference_id: "room", subject_description: "ok" }] }} minImages={1} maxImages={5} />);
    fireEvent.change(screen.getAllByRole("textbox")[0], { target: { value: "bad\nline" } });
    expect(screen.getByText(/必须为单行/)).toBeInTheDocument();
  });

  it("uploads an image, refreshes, and appends the candidate without overwriting its description", async () => {
    const uploaded = { reference_id: "new", source_kind: "temporary_upload" as const, label: "New", subject_description: "Uploaded subject", thumbnail_url: "/new.png" };
    m.upload.mockResolvedValue({ ok: true, data: uploaded });
    const onRefresh = vi.fn().mockResolvedValue({ ...preview, candidates: [...candidates, uploaded] });
    render(<GroupVideoReferenceDialog open onOpenChange={vi.fn()} project="p" episode={1} groupId="g" preview={{ ...preview, candidates: [candidates[0]], selected: [{ reference_id: "hero", subject_description: "Custom hero" }] }} minImages={1} maxImages={5} onRefresh={onRefresh} />);
    fireEvent.change(screen.getByLabelText("上传临时参考图"), { target: { files: [new File(["image"], "new.png", { type: "image/png" })] } });
    await waitFor(() => expect(m.upload).toHaveBeenCalledWith({ groupId: "g", file: expect.any(File) }));
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
    expect(await screen.findByDisplayValue("Uploaded subject")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Custom hero")).toBeInTheDocument();
  });

  it("keeps the dialog open and reports revision conflicts", async () => {
    m.save.mockRejectedValue(new Error("409 revision conflict"));
    const onOpenChange = vi.fn();
    render(<GroupVideoReferenceDialog open onOpenChange={onOpenChange} project="p" episode={1} groupId="g" preview={{ ...preview, selected: [{ reference_id: "hero", subject_description: "Hero" }] }} minImages={1} maxImages={5} />);
    fireEvent.click(screen.getByRole("button", { name: "保存参考图" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("已被其他操作更新");
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});
