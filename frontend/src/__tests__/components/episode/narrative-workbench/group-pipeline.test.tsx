import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GroupPipeline } from "@/components/episode/narrative-workbench/group-pipeline";
import type { NarrativeGroup } from "@/lib/queries/narrative-groups";

const group: NarrativeGroup = {
  id: "ng-01", ordinal: 1, title: "商场追逐", beat_ids: ["1", "2"],
  layout: { rows: 2, columns: 2, capacity: 4 },
  stages: {
    sketch: { status: "completed", revision: 1 },
    render: { status: "partial_failure", revision: 2 },
    video: { status: "pending", revision: 0 },
  },
  cell_to_beat: [{ cell: 0, beat_id: "1" }, { cell: 1, beat_id: "2" }],
  errors: [{ stage: "render", cell: 1, message: "split failed" }],
};

describe("GroupPipeline", () => {
  it("shows automatic split recovery separately from whole-group regeneration", () => {
    const action = vi.fn();
    render(<GroupPipeline group={group} onAction={action} onRepairBeat={vi.fn()} />);
    expect(screen.getByText("草图多宫格")).toBeInTheDocument();
    expect(screen.getByText("渲染图自动切分")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "仅重试切分" }));
    expect(action).toHaveBeenCalledWith("render", "split");
    const regenerateButtons = screen.getAllByRole("button", { name: "整组重新生成" });
    fireEvent.click(regenerateButtons[regenerateButtons.length - 1]!);
    expect(action).toHaveBeenCalledWith("render", "regenerate");
  });

  it("keeps whole-group regeneration available after completion", () => {
    const action = vi.fn();
    render(<GroupPipeline group={{ ...group, stages: { ...group.stages, render: { status: "completed", revision: 3 } } }} onAction={action} onRepairBeat={vi.fn()} />);
    const buttons = screen.getAllByRole("button", { name: "整组重新生成" });
    fireEvent.click(buttons[buttons.length - 1]!);
    expect(action).toHaveBeenCalledWith("render", "regenerate");
  });
});
