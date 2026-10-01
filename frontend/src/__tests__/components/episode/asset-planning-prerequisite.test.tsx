import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { assetPlanningBlockReason, AssetPlanningPrerequisite } from "@/components/episode/asset-planning-prerequisite";

describe("asset planning prerequisites", () => {
  it.each(["draft", "review_required", "superseded", "abandoned", "failed"])("blocks %s plans before paid submission", (status) => {
    expect(assetPlanningBlockReason({ isPending: false, isError: false, data: { ok: true, data: [{ status }] } })).toBe("先完成并激活镜头方案");
  });
  it("blocks empty, loading and failed reads, and permits an active revision alongside drafts", () => {
    expect(assetPlanningBlockReason({ isPending: false, isError: false, data: { ok: true, data: [] } })).toBe("先完成并激活镜头方案");
    expect(assetPlanningBlockReason({ isPending: true, isError: false })).toContain("正在检查");
    expect(assetPlanningBlockReason({ isPending: false, isError: true, data: { ok: true, data: [{ status: "active" }] } })).toContain("读取失败");
    expect(assetPlanningBlockReason({ isPending: false, isError: false, data: { ok: true, data: [{ status: "draft" }, { status: "active" }] } })).toBeNull();
  });
  it("offers planning and retry without bubbling into an episode card", async () => {
    const parent = vi.fn(), open = vi.fn(), retry = vi.fn();
    render(<div onClick={parent}><AssetPlanningPrerequisite reason="先完成并激活镜头方案" onOpenPlan={open} onRetry={retry} /></div>);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "进入剧本规划" }));
    await user.click(screen.getByRole("button", { name: "重试检查" }));
    expect(open).toHaveBeenCalledOnce(); expect(retry).toHaveBeenCalledOnce(); expect(parent).not.toHaveBeenCalled();
  });
});
