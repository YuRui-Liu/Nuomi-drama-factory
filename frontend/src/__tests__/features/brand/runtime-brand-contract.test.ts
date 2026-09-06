import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const readSource = (path: string) =>
  readFileSync(resolve(process.cwd(), path), "utf8");

describe("Nuomi Drama Factory runtime brand contract", () => {
  it("uses the full product name in assistant-facing text and as the chat display-name fallback", () => {
    const source = readSource("src/features/superchat/superchat-panel.tsx");

    expect(source).not.toMatch(/SuperTale(?:_N)?|DramaClaw\/SuperTale/);
    expect(source).toContain('displayName: username || "Nuomi Drama Factory"');
    expect(source).toContain("current Nuomi Drama Factory project ingest directory");
    expect(source).toContain("Nuomi Drama Factory video pipeline");
    expect(source).toContain("Nuomi Drama Factory video creation workflow");
  });

  it("uses the NuomiDrama brand for the viewer translate gizmo debug layer", () => {
    const source = readSource(
      "src/features/viewer-kit/three-d/engine/viewerApp.ts",
    );

    expect(source).not.toContain("SuperTale Translate Gizmo");
    expect(source).toContain("NuomiDrama Translate Gizmo");
  });
});
