import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TemporaryReferencePicker } from "@/components/episode/narrative-workbench/temporary-reference-picker";

describe("TemporaryReferencePicker", () => {
  it("uploads a file-only temporary reference and exposes an explicit remove action", async () => {
    const onUpload = vi.fn().mockResolvedValue({ uploadId: "upload-1", fileName: "雨夜参考.png", previewUrl: "/temporary/upload-1" });
    const onRemove = vi.fn();
    const { rerender } = render(<TemporaryReferencePicker uploads={[]} uploading={false} onUpload={onUpload} onRemove={onRemove} />);
    const file = new File(["image"], "雨夜参考.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("上传临时参考图"), { target: { files: [file] } });
    await waitFor(() => expect(onUpload).toHaveBeenCalledWith(file));
    rerender(<TemporaryReferencePicker uploads={[{ uploadId: "upload-1", fileName: "雨夜参考.png", previewUrl: "/temporary/upload-1" }]} uploading={false} onUpload={onUpload} onRemove={onRemove} />);
    expect(screen.getByText("雨夜参考.png")).toBeInTheDocument();
    expect(screen.getByText("仅本次生成")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "雨夜参考.png 预览" })).toHaveAttribute("src", "/temporary/upload-1");
    fireEvent.click(screen.getByRole("button", { name: "删除临时参考图 雨夜参考.png" }));
    expect(onRemove).toHaveBeenCalledWith("upload-1");
  });

  it("disables uploads when no image slots remain", () => {
    render(<TemporaryReferencePicker uploads={[]} uploading={false} disabled onUpload={vi.fn()} onRemove={vi.fn()} />);
    expect(screen.getByLabelText("上传临时参考图")).toBeDisabled();
    expect(screen.getByText("参考图数量已达上限")).toBeInTheDocument();
  });
});
