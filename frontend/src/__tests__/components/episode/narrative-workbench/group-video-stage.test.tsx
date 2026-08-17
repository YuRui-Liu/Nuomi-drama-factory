import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GroupVideoStage } from "@/components/episode/narrative-workbench/group-video-stage";
import { groupFrameSummary } from "@/components/episode/narrative-workbench/group-video-stage";

describe("GroupVideoStage", () => {
  it("shows inherited H3 and the actual automatic mode", () => {
    render(<GroupVideoStage modelId="runninghub:minimax-h3" mode="auto" hasFirstFrame hasLastFrame />);
    expect(screen.getByText(/MiniMax H3/)).toBeInTheDocument();
    expect(screen.getByText(/FL2V/)).toBeInTheDocument();
    expect(screen.getByText(/继承项目默认/)).toBeInTheDocument();
  });

  it("derives frame readiness and actual mode from every Beat, not render completion", () => {
    expect(groupFrameSummary([
      { beat_id: "1", has_first_frame: true, has_last_frame: true, actual_mode: "fl2va" },
      { beat_id: "2", has_first_frame: true, has_last_frame: false, actual_mode: "i2va" },
    ])).toEqual({ allHaveFirst: true, allHaveLast: false, modes: ["fl2va", "i2va"] });
  });

  it("keeps a temporary override local to the generation callback", () => {
    const generate = vi.fn();
    render(<GroupVideoStage modelId="runninghub:minimax-h3" mode="auto" hasFirstFrame hasLastFrame={false} onGenerate={generate} />);
    fireEvent.click(screen.getByRole("button", { name: "生成组内视频" }));
    expect(generate).toHaveBeenCalledWith({ video_model: "runninghub:minimax-h3", h3_mode: "auto" });
    expect(screen.getByText(/I2V/)).toBeInTheDocument();
  });
});
