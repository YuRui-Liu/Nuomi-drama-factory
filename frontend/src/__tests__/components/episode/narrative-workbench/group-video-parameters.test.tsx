import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { GroupVideoParameters } from "@/components/episode/narrative-workbench/group-video-parameters";
import type { GroupVideoParametersProps } from "@/components/episode/narrative-workbench/group-video-parameters";

it("summarizes effective scope before revealing editing controls", () => {
  const save = vi.fn();
  const parameters = [{ key: "resolution", label: "清晰度", default: "720p", options: [{ value: "720p", label: "720p" }, { value: "1080p", label: "1080p" }] }] as GroupVideoParametersProps["parameters"];
  render(<GroupVideoParameters parameters={parameters} projectDefaults={{ resolution: "720p" }} overrides={{ resolution: "1080p" }} aspectRatio="16:9" onSaveOverride={save} onRestoreDefault={vi.fn()} onPromoteDefault={vi.fn()} />);
  const disclosure = screen.getByText("视频参数").closest("details")!;
  expect(disclosure).not.toHaveAttribute("open");
  expect(screen.getByText("当前组覆盖 · 1080p")).toBeInTheDocument();
  fireEvent.click(screen.getByText("视频参数"));
  fireEvent.click(screen.getByRole("button", { name: /720p/ }));
  expect(save).toHaveBeenCalledWith("resolution", "720p");
});
