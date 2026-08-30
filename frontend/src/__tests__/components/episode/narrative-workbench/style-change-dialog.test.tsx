import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { StyleChangeDialog } from "@/components/episode/narrative-workbench/style-change-dialog";

describe("StyleChangeDialog", () => {
  it("offers restyle and redirection with an explicit group override", () => {
    const apply = vi.fn();
    render(<StyleChangeDialog
      open
      currentStyleId="style-default"
      availableStyles={[{ id: "style-default", label: "默认" }, { id: "style-ink", label: "水墨" }]}
      onOpenChange={vi.fn()}
      onApply={apply}
    />);

    fireEvent.change(screen.getByLabelText("叙事组风格"), { target: { value: "style-ink" } });
    fireEvent.click(screen.getByRole("button", { name: "仅换画风" }));
    expect(apply).toHaveBeenCalledWith({ styleId: "style-ink", action: "restyle" });

    fireEvent.click(screen.getByRole("button", { name: "按新风格重新导演" }));
    expect(apply).toHaveBeenLastCalledWith({ styleId: "style-ink", action: "redirect" });
  });

  it("can restore the inherited project default", () => {
    const apply = vi.fn();
    render(<StyleChangeDialog
      open
      currentStyleId="style-ink"
      availableStyles={[{ id: "style-ink", label: "水墨" }]}
      onOpenChange={vi.fn()}
      onApply={apply}
    />);

    fireEvent.click(screen.getByRole("button", { name: "恢复项目默认" }));
    expect(apply).toHaveBeenCalledWith({ styleId: null, action: "restyle" });
  });
});
