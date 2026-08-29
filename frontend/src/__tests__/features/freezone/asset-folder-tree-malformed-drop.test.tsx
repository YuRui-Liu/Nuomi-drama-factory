// SPDX-License-Identifier: Elastic-2.0
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
const organize = vi.hoisted(() => vi.fn());
vi.mock("@/lib/queries/asset-organization", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/queries/asset-organization")>(),
  useAssetFolders: () => ({ data: { ok: true, data: { folders: [{ id: "f1", name: "人物素材", asset_count: 0 }] } }, isLoading: false, isError: false }),
  useCreateAssetFolder: () => ({ mutateAsync: vi.fn(), isPending: false }), useRenameAssetFolder: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteAssetFolder: () => ({ mutateAsync: vi.fn(), isPending: false }), useOrganizeAsset: () => ({ mutateAsync: organize, isPending: false }),
}));
import { ASSET_ORGANIZATION_DRAG_MIME, AssetFolderTree } from "@/features/freezone/AssetFolderTree";
describe("AssetFolderTree malformed drop", () => {
  it("ignores malformed organization payloads", () => {
    render(<AssetFolderTree project="demo" selectedFolderId={undefined} selectedPurpose={null} onFolderChange={vi.fn()} onPurposeChange={vi.fn()} />);
    expect(() => fireEvent.drop(screen.getByTestId("folder-drop-f1"), { dataTransfer: { getData: (type: string) => type === ASSET_ORGANIZATION_DRAG_MIME ? "{" : "" } })).not.toThrow();
    expect(organize).not.toHaveBeenCalled();
  });
});
