// SPDX-License-Identifier: Elastic-2.0
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
describe("AssetLibraryPanel organization identity", () => {
  it("matches placements by the backend compound asset type and id key", () => {
    const source = readFileSync("src/features/freezone/AssetLibraryPanel.tsx", "utf8");
    expect(source).toContain('placement.asset_key ?? `${placement.asset_type}:${placement.asset_id}`');
    expect(source).toContain('`${organizationIdentity.assetType}:${organizationIdentity.assetId}`');
  });
});
