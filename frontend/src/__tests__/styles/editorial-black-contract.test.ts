// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const stylesheet = readFileSync("src/index.css", "utf8");

function block(selector: string) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = stylesheet.match(new RegExp(`${escaped}\\s*\\{([\\s\\S]*?)\\n\\}`, "m"));
  expect(match, `missing ${selector} block`).not.toBeNull();
  return match?.[1] ?? "";
}

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
    ["editorial-radius-xs", "4px"],
    ["editorial-radius-sm", "6px"],
    ["editorial-radius-md", "8px"],
    ["editorial-radius-lg", "10px"],
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

  it("provides a base-layer keyboard focus ring that utilities can override", () => {
    expect(stylesheet).toMatch(
      /@layer base\s*\{\s*:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--editorial-focus\)[^}]*outline-offset:\s*2px/s,
    );
    expect(stylesheet).not.toMatch(/^:focus-visible\s*\{/m);
  });

  it("keeps the default semantic theme light for persisted theme compatibility", () => {
    const root = block(":root");
    expect(root).toContain("--background: oklch(0.9605 0.0046 258.3248)");
    expect(root).toContain("--foreground: oklch(0.2153 0.0187 235.1251)");
    expect(root).toContain("--card: oklch(1.0000 0 0)");
    expect(root).toContain("color-scheme: light");
  });

  it("maps only the dark semantic theme to Editorial Black", () => {
    const dark = block(".dark");
    expect(dark).toContain("--background: var(--editorial-background)");
    expect(dark).toContain("--primary: var(--editorial-brand)");
    expect(dark).toContain("--ring: var(--editorial-focus)");
    expect(dark).toContain("color-scheme: dark");
  });

  it("maps Tailwind radii to the four uniquely named tiers", () => {
    const theme = block("@theme inline");
    expect(theme).toContain("--radius-sm: var(--editorial-radius-xs)");
    expect(theme).toContain("--radius-md: var(--editorial-radius-sm)");
    expect(theme).toContain("--radius-lg: var(--editorial-radius-md)");
    expect(theme).toContain("--radius-xl: var(--editorial-radius-lg)");
  });

  it("uses the approved UI and timeline font stacks", () => {
    const theme = block("@theme inline");
    expect(theme).toMatch(/--font-sans:[^;]*Inter Variable[^;]*Noto Sans SC/);
    expect(theme).toMatch(/--font-mono:[^;]*Geist Mono/);
  });

  it("disables non-essential animation for reduced-motion users", () => {
    expect(stylesheet).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)/);
    expect(stylesheet).toMatch(/transition-duration:\s*0\.01ms\s*!important/);
    expect(stylesheet).toMatch(/animation-iteration-count:\s*1\s*!important/);
  });
});
