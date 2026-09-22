import { fireEvent, render, screen, within } from "@testing-library/react";
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
    fireEvent.click(within(screen.getByText("渲染多宫格").closest("section")!).getByRole("button", { name: "整组重新生成" }));
    expect(action).toHaveBeenCalledWith("render", "regenerate");
  });

  it("keeps whole-group regeneration available after completion", () => {
    const action = vi.fn();
    render(<GroupPipeline group={{ ...group, stages: { ...group.stages, render: { status: "completed", revision: 3 } } }} onAction={action} onRepairBeat={vi.fn()} />);
    fireEvent.click(within(screen.getByText("渲染多宫格").closest("section")!).getByRole("button", { name: "整组重新生成" }));
    expect(action).toHaveBeenCalledWith("render", "regenerate");
  });

  it("starts with render and leaves unused sketches collapsed", () => {
    const action = vi.fn();
    render(<GroupPipeline group={{ ...group, stages: { ...group.stages, sketch: { status: "pending", revision: 0 }, render: { status: "pending", revision: 0 } } }} onAction={action} onRepairBeat={vi.fn()} />);
    expect(screen.getByText("草图多宫格").closest("details")).not.toHaveAttribute("open");
    // jsdom does not consistently exclude children of closed native details from roles.
    const renderSection = screen.getByText("渲染多宫格").closest("section")!;
    expect(renderSection.compareDocumentPosition(screen.getByText("草图多宫格")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(within(renderSection).getByRole("button", { name: "开始生成" }));
    expect(action).toHaveBeenCalledWith("render", "generate");
  });

  it("disables regeneration while an image request is in flight", () => {
    const action = vi.fn();
    render(<GroupPipeline group={{ ...group, stages: { ...group.stages, render: { status: "running", revision: 3 } } }} onAction={action} onRepairBeat={vi.fn()} />);
    const button = within(screen.getByText("渲染多宫格").closest("section")!).getByRole("button", { name: "整组重新生成" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(action).not.toHaveBeenCalled();
  });
});
