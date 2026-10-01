import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProductionStyleBridge } from "./production-style-bridge";

const mocks = vi.hoisted(() => ({ apply: vi.fn(), current: "custom_existing" }));
vi.mock("@/lib/queries/projects", () => ({
  useProject: () => ({ data: { data: { visual_style: mocks.current } } }),
  useUpdateProject: () => ({ mutateAsync: mocks.apply, isPending: false }),
}));
vi.mock("@/lib/queries/styles", () => ({ useStyles: () => ({ data: { data: [
  { id: "custom_existing", label: "已确认自定义画风" },
  { id: "guoman_fantasy", label: "3D玄幻国漫" },
  { id: "drama_ext.guoman_3d_animation", label: "3D 国漫动画" },
] } }) }));

describe("ProductionStyleBridge", () => {
  beforeEach(() => { mocks.apply.mockReset().mockResolvedValue({ ok: true }); });
  it("applies an exact registered creative style without fantasy substitution", async () => {
    render(<ProductionStyleBridge project="demo" briefStyle={["3D 国漫动画"]} />);
    fireEvent.click(screen.getByRole("button", { name: "将创作画风应用到素材生成" }));
    await waitFor(() => expect(mocks.apply).toHaveBeenCalled());
    expect(mocks.apply).toHaveBeenCalledWith({ visual_style: "drama_ext.guoman_3d_animation" });
  });
  it("does not guess a production style for unmatched or multiple creative styles", () => {
    render(<ProductionStyleBridge project="demo" briefStyle={["3D 国漫动画", "水墨"]} />);
    expect(screen.queryByRole("button", { name: "将创作画风应用到素材生成" })).not.toBeInTheDocument();
    expect(mocks.apply).not.toHaveBeenCalled();
  });
  it("preserves existing production style until explicitly applied", async () => {
    render(<ProductionStyleBridge project="demo" briefStyle={["3D 国漫动画"]} />);
    expect(screen.getByText("创作画风：3D 国漫动画")).toBeInTheDocument();
    expect(screen.getByLabelText("素材生成画风")).toHaveValue("custom_existing");
    expect(mocks.apply).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("素材生成画风"), { target: { value: "guoman_fantasy" } });
    expect(mocks.apply).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "应用到素材生成" }));
    await waitFor(() => expect(mocks.apply).toHaveBeenCalledWith({ visual_style: "guoman_fantasy" }));
  });
  it("keeps a failed change visible and retryable", async () => {
    mocks.apply.mockRejectedValue(new Error("保存失败"));
    render(<ProductionStyleBridge project="demo" briefStyle={[]} />);
    fireEvent.change(screen.getByLabelText("素材生成画风"), { target: { value: "guoman_fantasy" } });
    fireEvent.click(screen.getByRole("button", { name: "应用到素材生成" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("保存失败");
    expect(screen.getByLabelText("素材生成画风")).toHaveValue("guoman_fantasy");
  });
});
