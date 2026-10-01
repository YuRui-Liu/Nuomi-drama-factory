import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { NarrativeGroupList } from "@/components/episode/narrative-workbench/narrative-group-list";
import type { NarrativeGroup } from "@/lib/queries/narrative-groups";

function group(overrides: Partial<NarrativeGroup>): NarrativeGroup {
  return {
    id: "group-1",
    ordinal: 1,
    title: null,
    beat_ids: ["line-7", "line-8"],
    layout: { rows: 1, columns: 2, capacity: 2 },
    stages: {
      sketch: { status: "pending", revision: 0 },
      render: { status: "pending", revision: 0 },
      video: { status: "pending", revision: 0 },
    },
    cell_to_beat: [],
    errors: [],
    ...overrides,
  };
}

describe("NarrativeGroupList", () => {
  it("counts production shots rather than source lines and labels the fallback honestly", () => {
    const { rerender } = render(<NarrativeGroupList groups={[group({ beat_ids: ["line-7", "line-8", "line-9"], shot_ids: ["shot-01-01", "shot-01-02"] })]} selectedId="group-1" onSelect={vi.fn()} />);
    expect(screen.getByText("2 个镜头")).toBeInTheDocument();
    expect(screen.queryByText("3 个镜头")).not.toBeInTheDocument();
    rerender(<NarrativeGroupList groups={[group({ beat_ids: ["line-7", "line-8", "line-9"] })]} selectedId="group-1" onSelect={vi.fn()} />);
    expect(screen.getByText("3 个来源片段")).toBeInTheDocument();
  });
  it("shows production progress and keeps selection accessible", () => {
    const select = vi.fn();
    const ready = group({ id: "ready", stages: {
      sketch: { status: "pending", revision: 0 },
      render: { status: "completed", revision: 1 },
      video: { status: "completed", revision: 1 },
    } });
    render(<NarrativeGroupList groups={[group({}), ready]} selectedId="group-1" onSelect={select} />);
    expect(screen.getByText("视频完成 1 / 2")).toBeInTheDocument();
    expect(screen.getByText("待制作分镜").closest("button")).toHaveAttribute("aria-current", "true");
    fireEvent.click(screen.getByText("视频已完成"));
    expect(select).toHaveBeenCalledWith("ready");
  });
  it("shows a readable objective while keeping source span ids secondary", () => {
    render(
      <NarrativeGroupList
        groups={[
          group({
            title: "Beat line-7–line-8",
            objective: "石九推门进入祠堂，发现供桌后的血迹",
            visible_turn: "门外脚步声突然逼近",
            source_span_ids: ["line-7", "line-8"],
          }),
        ]}
        selectedId="group-1"
        onSelect={vi.fn()}
      />,
    );

    const item = screen.getByRole("button", { name: /叙事组 01/ });
    expect(within(item).getByText("石九推门进入祠堂，发现供桌后的血迹")).toBeInTheDocument();
    expect(within(item).getByText("来源：line-7–line-8")).toBeInTheDocument();
  });

  it("keeps an existing readable title ahead of generated descriptions", () => {
    render(
      <NarrativeGroupList
        groups={[
          group({ title: "雨夜祠堂惊变", objective: "石九推门进入祠堂" }),
        ]}
        selectedId={null}
        onSelect={vi.fn()}
      />,
    );

    expect(screen.getByText("雨夜祠堂惊变")).toBeInTheDocument();
  });
});
