// SPDX-License-Identifier: Elastic-2.0
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  create: vi.fn(), rename: vi.fn(), remove: vi.fn(), organize: vi.fn(), refetchFolders: vi.fn(),
  foldersState: { data: { ok: true, data: { folders: [{ id: "f1", name: "人物素材", asset_count: 2 }] } } as unknown, isLoading: false, isError: false, error: null as Error | null },
}));
vi.mock("@/lib/queries/asset-organization", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/queries/asset-organization")>(),
  useAssetFolders: () => ({ ...mocks.foldersState, refetch: mocks.refetchFolders }),
  useCreateAssetFolder: () => ({ mutateAsync: mocks.create, isPending: false }),
  useRenameAssetFolder: () => ({ mutateAsync: mocks.rename, isPending: false }),
  useDeleteAssetFolder: () => ({ mutateAsync: mocks.remove, isPending: false }),
  useOrganizeAsset: () => ({ mutateAsync: mocks.organize, isPending: false }),
}));
import { ASSET_ORGANIZATION_DRAG_MIME, AssetFolderTree } from "@/features/freezone/AssetFolderTree";

beforeEach(() => {
  vi.restoreAllMocks();
  for (const key of ["create", "rename", "remove", "organize", "refetchFolders"] as const) mocks[key].mockReset().mockResolvedValue({ ok: true });
  mocks.foldersState.data = { ok: true, data: { folders: [{ id: "f1", name: "人物素材", asset_count: 2 }] } };
  mocks.foldersState.isLoading = false; mocks.foldersState.isError = false; mocks.foldersState.error = null;
});
const renderTree = () => render(<AssetFolderTree project="demo" selectedFolderId={undefined} selectedPurpose={null} onFolderChange={vi.fn()} onPurposeChange={vi.fn()} />);

describe("AssetFolderTree", () => {
  it("supports folder CRUD and exact-id organization", async () => {
    vi.spyOn(window, "prompt").mockReturnValueOnce("场景素材").mockReturnValueOnce("主场景库");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup(); renderTree();
    await user.click(screen.getByRole("button", { name: "新建目录" }));
    await user.click(screen.getByRole("button", { name: "重命名 人物素材" }));
    await user.click(screen.getByRole("button", { name: "删除 人物素材" }));
    const payload = { assetType: "image", assetId: "characters:hero:portrait.png", purpose: "character" };
    fireEvent.drop(screen.getByTestId("folder-drop-f1"), { dataTransfer: { getData: (type: string) => type === ASSET_ORGANIZATION_DRAG_MIME ? JSON.stringify(payload) : "" } });
    expect(mocks.create).toHaveBeenCalledWith({ name: "场景素材" });
    expect(mocks.rename).toHaveBeenCalledWith({ folderId: "f1", name: "主场景库" });
    expect(mocks.remove).toHaveBeenCalledWith({ folderId: "f1" });
    expect(mocks.organize).toHaveBeenCalledWith({ ...payload, folder_id: "f1" });
  });
  it("shows query errors and retries instead of presenting an empty folder response", async () => {
    mocks.foldersState.data = undefined; mocks.foldersState.isError = true; mocks.foldersState.error = new Error("Request timed out");
    const user = userEvent.setup(); renderTree();
    expect(screen.getByRole("alert")).toHaveTextContent("目录加载失败：Request timed out");
    expect(screen.queryByText("人物素材")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试目录" }));
    expect(mocks.refetchFolders).toHaveBeenCalledTimes(1);
  });
  it("catches mutation failures and makes them visible", async () => {
    mocks.create.mockRejectedValueOnce(new Error("保存超时")); vi.spyOn(window, "prompt").mockReturnValue("失败目录");
    const user = userEvent.setup(); renderTree(); await user.click(screen.getByRole("button", { name: "新建目录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("目录操作失败：保存超时");
  });
});
