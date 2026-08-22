// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const stylesheet = readFileSync("src/index.css", "utf8");

function expectToken(name: string, value: string) {
  expect(stylesheet).toMatch(new RegExp(`--${name}\\s*:\\s*${value}\\s*;`, "i"));
}

describe("Editorial Black design token contract", () => {
  it.each([
    ["editorial-background", "#0D0E10"],
    ["editorial-surface-1", "#14161A"],
    ["editorial-surface-2", "#1A1D22"],
    ["editorial-raised", "#20242A"],
    ["editorial-border", "#2A2F37"],
    ["editorial-text", "#F2F4F7"],
    ["editorial-muted", "#9299A5"],
    ["editorial-brand", "#E5FF5C"],
    ["editorial-focus", "#BFE6FF"],
    ["editorial-success", "#52D89B"],
    ["editorial-warning", "#F3B85B"],
    ["editorial-danger", "#F06B72"],
  ])("defines %s as the approved palette value", (name, value) => {
    expectToken(name, value);
  });

  it.each([
    ["radius-xs", "4px"],
    ["radius-sm", "6px"],
    ["radius-md", "8px"],
    ["radius-lg", "10px"],
  ])("defines the %s radius tier", (name, value) => {
    expectToken(name, value);
  });

  it.each([
    ["header-height", "56px"],
    ["sidebar-width", "232px"],
    ["control-height-sm", "32px"],
    ["control-height-md", "36px"],
    ["control-height-lg", "40px"],
  ])("defines the %s layout metric", (name, value) => {
    expectToken(name, value);
  });

  it("provides a visible two-pixel keyboard focus ring", () => {
    expect(stylesheet).toMatch(/:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--editorial-focus\)/s);
    expect(stylesheet).toMatch(/:focus-visible\s*\{[^}]*outline-offset:\s*2px/s);
  });

  it("disables non-essential animation for reduced-motion users", () => {
    expect(stylesheet).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)/);
    expect(stylesheet).toMatch(/transition-duration:\s*0\.01ms\s*!important/);
    expect(stylesheet).toMatch(/animation-iteration-count:\s*1\s*!important/);
  });
});
