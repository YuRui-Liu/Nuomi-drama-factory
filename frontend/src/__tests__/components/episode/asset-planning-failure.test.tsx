import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AssetPlanningFailure } from "@/components/episode/asset-planning-failure";

describe("asset planning failure recovery", () => {
  it("retains exact missing locations and directs users to scene registration", async () => {
    const open = vi.fn(), parent = vi.fn();
    render(<div onClick={parent}><AssetPlanningFailure kind="scene" error="BASE_SCENE_IMPORT_REQUIRED: 第 1 集缺失基础场景：林家门口、居民院、巷子" onOpenAssets={open} /></div>);
    expect(screen.getByRole("alert")).toHaveTextContent("林家门口、居民院、巷子");
    expect(screen.getByRole("alert")).toHaveTextContent("登记或导入以上基础场景");
    await userEvent.setup().click(screen.getByRole("button", { name: "前往资产中心 · 场景" }));
    expect(open).toHaveBeenCalledWith("scene");
    expect(parent).not.toHaveBeenCalled();
  });
  it.each([["character", "人物", "identity"], ["prop", "道具", "prop"]] as const)("offers the correct %s catalog after a planning failure", async (kind, label, destination) => {
    const open = vi.fn();
    const view = render(<AssetPlanningFailure kind={kind} error="原始失败原因" onOpenAssets={open} />);
    expect(screen.getByRole("alert")).toHaveTextContent("原始失败原因");
    await userEvent.setup().click(screen.getByRole("button", { name: `前往资产中心 · ${label}` }));
    expect(open).toHaveBeenCalledWith(destination);
    view.rerender(<AssetPlanningFailure kind={kind} error={null} onOpenAssets={open} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
