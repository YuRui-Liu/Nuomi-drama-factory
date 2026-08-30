import { describe, expect, it } from "vitest";

import type { EffectiveStyleSnapshot } from "@/lib/queries/narrative-groups";

describe("style contract v2", () => {
  it("represents inherited project defaults without nullable identity fields", () => {
    const snapshot: EffectiveStyleSnapshot = {
      snapshot_id: "snapshot-default",
      style_id: "project-default",
      style_version: "1",
      catalog_hash: "catalog",
      style_hash: "style",
      inherited: true,
    };

    expect(snapshot.inherited).toBe(true);
    expect(snapshot.style_id).toBe("project-default");
  });
});
