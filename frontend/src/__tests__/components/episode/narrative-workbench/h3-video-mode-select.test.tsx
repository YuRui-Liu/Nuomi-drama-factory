import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { H3VideoModeSelect } from "@/components/episode/narrative-workbench/h3-video-mode-select";
import { h3ModeAvailabilities } from "@/lib/queries/media-models";

it("shows unsupported modes with stable reasons and changes only to enabled modes", async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  const availability = h3ModeAvailabilities({
    id: "runninghub:minimax-h3",
    label: "H3",
    provider: "runninghub",
    available: true,
    supported_modes: ["i2va", "fl2va"],
    default_mode: "auto",
    parameters: [],
  }, {
    hasFirstFrame: true,
    hasLastFrame: false,
    referenceCount: 0,
  });

  render(<H3VideoModeSelect
    value="auto"
    availability={availability}
    onChange={onChange}
  />);

  const select = screen.getByRole("combobox", { name: "H3 视频模式" });
  expect(screen.getByRole("option", { name: /自动.*I2V/ })).toBeEnabled();
  expect(screen.getByRole("option", { name: /^FL2V.*缺输入/ })).toBeDisabled();
  expect(screen.getByRole("option", { name: /^L2V.*缺输入/ })).toBeDisabled();
  expect(screen.getByRole("option", { name: /^Ref2V.*缺输入/ })).toBeDisabled();

  await user.selectOptions(select, "i2va");
  expect(onChange).toHaveBeenCalledWith("i2va");
});

it("keeps an input-valid but unsupported mode visible with the capability reason", () => {
  const availability = h3ModeAvailabilities({
    id: "runninghub:minimax-h3",
    label: "H3",
    provider: "runninghub",
    available: true,
    supported_modes: ["i2va", "fl2va"],
    default_mode: "auto",
    parameters: [],
  }, {
    hasFirstFrame: false,
    hasLastFrame: true,
    referenceCount: 0,
  });

  render(<H3VideoModeSelect
    value="auto"
    availability={availability}
    onChange={vi.fn()}
  />);

  expect(screen.getByRole("option", { name: /^L2V.*模型不支持/ })).toBeDisabled();
});
