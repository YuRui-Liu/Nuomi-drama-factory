import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FreeReferencePicker } from "@/components/episode/narrative-workbench/free-reference-picker";

describe("FreeReferencePicker", () => {
  it("selects a project candidate as an additional reference", () => {
    const onAdd = vi.fn();
    render(<FreeReferencePicker candidates={[{
      id: "prop-lantern", kind: "prop", label: "旧灯笼", available: true,
      thumbnail_url: "/lantern.png",
    }]} onAdd={onAdd} onUpload={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "追加 旧灯笼" }));
    expect(onAdd).toHaveBeenCalledWith("prop-lantern");
  });

  it("uploads temporarily by default and exposes persistence target fields", () => {
    const onUpload = vi.fn();
    render(<FreeReferencePicker candidates={[]} onAdd={vi.fn()} onUpload={onUpload} />);
    const file = new File(["image"], "reference.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("上传本地参考图"), { target: { files: [file] } });
    expect(onUpload).toHaveBeenCalledWith(file, expect.objectContaining({ persist: false }));

    fireEvent.click(screen.getByRole("checkbox", { name: /保存到项目资产/ }));
    expect(screen.getByLabelText("资产类型")).toBeInTheDocument();
    expect(screen.getByLabelText("目标实体 ID")).toBeInTheDocument();
  });
});
