import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GroupBeatInspector } from "@/components/episode/narrative-workbench/group-beat-inspector";
import type { NarrativeGroup } from "@/lib/queries/narrative-groups";

const group: NarrativeGroup = {
  id: "ng-01", ordinal: 1, beat_ids: ["1", "2"],
  layout: { rows: 1, columns: 2, capacity: 2 },
  stages: {
    sketch: { status: "completed", revision: 1 },
    render: {
      status: "partial_failure", revision: 2,
      cell_assets: [
        { cell: 0, beat_id: "1", url: "/static/demo/render/cell-1.png" },
        { cell: 1, beat_id: "2", error: "切分失败" },
      ],
    },
    video: { status: "pending", revision: 0 },
  },
  cell_to_beat: [{ cell: 0, beat_id: "1" }, { cell: 1, beat_id: "2" }],
  video_inputs: [],
  errors: [],
};

describe("GroupBeatInspector", () => {
  it("renders canonical cell asset URLs and per-cell failures", () => {
    const repair = vi.fn();
    render(<GroupBeatInspector group={group} onRepairBeat={repair} />);
    expect(screen.getByAltText("Beat 1 渲染格位")).toHaveAttribute("src", "/static/demo/render/cell-1.png");
    expect(screen.getByText("切分失败")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "单 Beat 修复" })[1]);
    expect(repair).toHaveBeenCalledWith("2");
  });
});
