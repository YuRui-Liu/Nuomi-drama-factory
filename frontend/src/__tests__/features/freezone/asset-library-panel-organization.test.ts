// SPDX-License-Identifier: Elastic-2.0
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
describe("AssetLibraryPanel organization integration", () => {
  const source = readFileSync("src/features/freezone/AssetLibraryPanel.tsx", "utf8");
  it("combines folder and purpose placement filters", () => {
    expect(source).toContain("useAssetOrganization(project)"); expect(source).toContain("placement?.folder_id === selectedFolderId"); expect(source).toContain("placement?.purpose === selectedPurpose"); expect(source).toContain("<AssetFolderTree");
  });
  it("preserves canvas drag and adds organization metadata", () => {
    expect(source).toContain("event.dataTransfer.setData(CANVAS_ASSET_DRAG_MIME, JSON.stringify(dragPayload));"); expect(source).toContain("ASSET_ORGANIZATION_DRAG_MIME"); expect(source).toContain("assetId: asset.id");
  });
  it("surfaces organization failures with an explicit retry", () => {
    expect(source).toContain("organizationQuery.isError"); expect(source).toContain("素材归类加载失败"); expect(source).toContain("organizationQuery.refetch()"); expect(source).toContain("重试归类");
  });
  it("never treats a failed organization request as an empty placement result", () => {
    expect(source).toContain("lastSuccessfulOrganizationRef");
    expect(source).toContain("organizationHasUsableData");
    expect(source).toContain("if (!organizationHasUsableData) return []");
    expect(source).toContain("!organizationHasUsableData ? null : error ?");
  });
});
