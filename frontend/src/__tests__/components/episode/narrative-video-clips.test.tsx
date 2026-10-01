import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NarrativeVideoClips } from "@/components/episode/narrative-video-clips";
import type { NarrativeGroup } from "@/lib/queries/narrative-groups";

function group(status: string, asset = "", error = ""): NarrativeGroup {
  return { id: "g1", ordinal: 1, title: "白尾漏雨", beat_ids: ["s1"],
    stages: { video: { status, video_asset: asset, error } },
  } as NarrativeGroup;
}

describe("narrative video clips", () => {
  it("explains continuity rejection without presenting raw JSON as the main message", () => {
    render(<NarrativeVideoClips groups={[group("failed", "", JSON.stringify({ error_code: "H3_CONTINUITY_QUALITY_REJECTED", transport_called: false }))]} />);
    expect(screen.getByText("镜头连续性检查未通过，尚未提交视频生成。请前往镜头页补齐所需画面并调整镜头设计。")).toBeInTheDocument();
    expect(screen.getByText("查看错误详情")).toBeInTheDocument();
  });
  it("previews an existing video even after a failed regeneration", () => {
    const { container } = render(<NarrativeVideoClips groups={[group("failed", "/media/clip.mp4", "生成超时")]} />);
    expect(container.querySelector("video")).toHaveAttribute("src", "/media/clip.mp4");
    expect(screen.getByText("生成超时")).toBeInTheDocument();
    expect(screen.getByText("保留的已有片段")).toBeInTheDocument();
  });
  it("distinguishes pending and failed groups from successful clips", () => {
    const { container } = render(<NarrativeVideoClips groups={[group("failed", "", "参考图失效"), { ...group("pending"), id: "g2", ordinal: 2 }]} />);
    expect(container.querySelector("video")).toBeNull();
    expect(screen.getByText("参考图失效")).toBeInTheDocument();
    expect(screen.getByText("待生成")).toBeInTheDocument();
    expect(screen.getByText("0 / 2 个叙事组有视频片段")).toBeInTheDocument();
  });
  it("offers retry for a failed request and navigation to generation", async () => {
    const retry = vi.fn(); const open = vi.fn();
    render(<NarrativeVideoClips groups={[]} error onRetry={retry} onOpenWorkbench={open} />);
    expect(screen.getByText("视频片段加载失败")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    await userEvent.click(screen.getByRole("button", { name: "前往镜头生成" }));
    expect(retry).toHaveBeenCalledOnce(); expect(open).toHaveBeenCalledOnce();
  });
  it("does not describe loading as no generated video", () => {
    render(<NarrativeVideoClips groups={[]} loading />);
    expect(screen.getByText("正在加载视频片段…")).toBeInTheDocument();
    expect(screen.queryByText("暂无生成的视频片段")).not.toBeInTheDocument();
  });
});
