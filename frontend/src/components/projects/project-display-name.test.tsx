import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProjectDisplayName } from "./project-display-name";

describe("ProjectDisplayName", () => {
  it("saves a Chinese display name without changing the internal identifier", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    render(<ProjectDisplayName name="mijing_shanhai" save={save} />);
    fireEvent.change(screen.getByLabelText("作品显示名称"), { target: { value: "秘境山海" } });
    fireEvent.click(screen.getByRole("button", { name: "保存名称" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith({ display_name: "秘境山海" }));
    expect(screen.getByText("内部标识：mijing_shanhai")).toBeTruthy();
  });
});
