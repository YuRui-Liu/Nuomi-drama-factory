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

  it("opens a rendered cell image in the lightbox", () => {
    render(<GroupBeatInspector group={group} onRepairBeat={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Beat 1 渲染格位" }));

    expect(screen.getByRole("link", { name: "Download image" })).toHaveAttribute(
      "href",
      "/static/demo/render/cell-1.png",
    );
    expect(screen.getAllByAltText("Beat 1 渲染格位")).toHaveLength(2);
  });

  it("never falls back to whole-group rendering for a shot id", () => {
    const regenerate = vi.fn();
    render(
      <GroupBeatInspector
        group={{
          ...group,
          beat_ids: ["shot-01-01"],
          cell_to_beat: [{ cell: 0, beat_id: "shot-01-01" }],
          stages: {
            ...group.stages,
            render: {
              status: "completed",
              revision: 2,
              cell_assets: [{ cell: 0, beat_id: "shot-01-01", url: "/static/demo/render/cell-1.png" }],
            },
          },
        }}
        onRegenerateStage={regenerate}
      />,
    );

    expect(screen.queryByRole("button", { name: "单 Beat 修复" })).toBeNull();
    const button = screen.queryByRole("button", { name: "重新生成该组（实图）" });
    expect(screen.queryByRole("button", { name: "重新生成该组（草图）" })).toBeNull();
    expect(button).toBeNull();
    expect(regenerate).not.toHaveBeenCalled();
  });

  it("never falls back to whole-group regeneration for a sketch cell", () => {
    const regenerate = vi.fn();
    render(
      <GroupBeatInspector
        group={{
          ...group,
          beat_ids: ["shot-01-01"],
          cell_to_beat: [{ cell: 0, beat_id: "shot-01-01" }],
          stages: {
            ...group.stages,
            render: { status: "pending", revision: 0 },
            sketch: {
              status: "completed",
              revision: 1,
              cell_assets: [{ cell: 0, beat_id: "shot-01-01", url: "/static/demo/sketch/cell-1.png" }],
            },
          },
        }}
        onRegenerateStage={regenerate}
      />,
    );

    expect(screen.queryByRole("button", { name: "单 Beat 修复" })).toBeNull();
    const button = screen.queryByRole("button", { name: "重新生成该组（草图）" });
    expect(screen.queryByRole("button", { name: "重新生成该组（实图）" })).toBeNull();
    expect(button).toBeNull();
    expect(regenerate).not.toHaveBeenCalled();
  });

  it("renders no action button when neither a beat repair nor a regeneration is available", () => {
    render(
      <GroupBeatInspector
        group={{
          ...group,
          beat_ids: ["shot-01-01"],
          cell_to_beat: [{ cell: 0, beat_id: "shot-01-01" }],
          stages: {
            ...group.stages,
            render: {
              status: "completed",
              revision: 2,
              cell_assets: [{ cell: 0, beat_id: "shot-01-01", url: "/static/demo/render/cell-1.png" }],
            },
          },
        }}
      />,
    );

    expect(screen.queryByRole("button", { name: "单 Beat 修复" })).toBeNull();
    expect(screen.queryByRole("button", { name: "重新生成该组（实图）" })).toBeNull();
    expect(screen.queryByRole("button", { name: "重新生成该组（草图）" })).toBeNull();
    // Only the lightbox trigger remains; no per-cell action is rendered.
    expect(screen.getAllByRole("button")).toHaveLength(1);
  });
});
