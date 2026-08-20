import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { NarrativeAspectSelector } from "@/components/episode/narrative-workbench/narrative-aspect-selector";

describe("NarrativeAspectSelector", () => {
  it("exposes one pressed aspect and submits the other orientation", () => {
    const onChange = vi.fn();
    render(
      <NarrativeAspectSelector
        orientation="portrait"
        saving={false}
        onChange={onChange}
      />,
    );

    expect(screen.getByRole("group", { name: "目标画幅" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "9:16" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "16:9" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );

    fireEvent.click(screen.getByRole("button", { name: "16:9" }));
    expect(onChange).toHaveBeenCalledWith("landscape");
    expect(
      screen.getByText("仅影响后续生成；现有素材需重新生成或重新切分。"),
    ).toBeInTheDocument();
  });

  it("disables both choices while saving", () => {
    render(
      <NarrativeAspectSelector
        orientation="landscape"
        saving
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "9:16" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "16:9" })).toBeDisabled();
  });
});
