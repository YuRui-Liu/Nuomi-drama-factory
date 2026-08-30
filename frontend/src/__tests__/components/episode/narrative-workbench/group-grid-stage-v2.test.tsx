import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GenerationBatchSummary } from "@/components/episode/narrative-workbench/group-grid-stage";
import { GroupVideoSegmentList } from "@/components/episode/narrative-workbench/group-video-segment-list";

describe("narrative production batches and segments", () => {
  it("shows the real batch layout and generation metadata", () => {
    render(<GenerationBatchSummary batches={[{
      id: "batch-1",
      group_id: "ng-01",
      shot_ids: ["shot-1", "shot-2"],
      layout: "diptych",
      rows: 1,
      columns: 2,
      capacity: 2,
      style_snapshot_id: "snapshot-1",
      status: "completed",
      provider: "grsai-main",
      model: "gpt-image-2-vip",
      requested_resolution: "2K",
      actual_resolution: "2160x3840",
      style_hash: "style-hash-abc",
      cleanup_reports: [{ remaining_bright_border_ratio: 0.004 }],
    }]} />);

    expect(screen.getByText("二联画 · 2 个镜头")).toBeInTheDocument();
    expect(screen.getByText(/grsai-main\/gpt-image-2-vip/)).toBeInTheDocument();
    expect(screen.getByText(/2K.*2160x3840/)).toBeInTheDocument();
    expect(screen.getByText(/style-hash-abc/)).toBeInTheDocument();
    expect(screen.getByText(/亮边 0.40%/)).toBeInTheDocument();
  });

  it("retries only the failed video segment", () => {
    const retry = vi.fn();
    render(<GroupVideoSegmentList
      segments={[
        { id: "seg-01", group_id: "ng-01", shot_ids: ["shot-1"], duration_seconds: 4, continuity_reason: "single_shot", audio_mode: "h3_original", style_snapshot_id: "snapshot-1", status: "completed" },
        { id: "seg-02", group_id: "ng-01", shot_ids: ["shot-2"], duration_seconds: 5, continuity_reason: "single_shot", audio_mode: "external_tts", style_snapshot_id: "snapshot-1", status: "failed", error: "provider failed" },
      ]}
      onRetrySegment={retry}
    />);

    fireEvent.click(screen.getByRole("button", { name: "重试片段 seg-02" }));
    expect(retry).toHaveBeenCalledTimes(1);
    expect(retry).toHaveBeenCalledWith("seg-02");
    expect(screen.queryByRole("button", { name: "重试片段 seg-01" })).not.toBeInTheDocument();
  });
});
