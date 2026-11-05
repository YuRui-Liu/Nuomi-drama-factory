// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("ImageGenNode aspect option", () => {
  it("offers 2:1 as an exact control option instead of highlighting a nearby preset", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/features/canvas/nodes/ImageGenNode.tsx"),
      "utf8",
    );

    expect(source).toContain("{ value: '2:1', label: '2:1' }");
  });
});
