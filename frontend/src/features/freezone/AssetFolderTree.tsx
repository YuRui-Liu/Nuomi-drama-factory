// SPDX-License-Identifier: Elastic-2.0
import { useState } from "react";
import { type AssetPurpose, useAssetFolders, useCreateAssetFolder, useDeleteAssetFolder, useOrganizeAsset, useRenameAssetFolder } from "@/lib/queries/asset-organization";
import { cn } from "@/lib/utils";

export const ASSET_ORGANIZATION_DRAG_MIME = "application/x-nuomi-asset-organization";
export interface AssetOrganizationDragPayload { assetType: string; assetId: string; purpose: AssetPurpose; }
const PURPOSES: Array<{ value: AssetPurpose | null; label: string }> = [
  { value: null, label: "全部用途" }, { value: "character", label: "角色用途" }, { value: "scene", label: "布景用途" },
  { value: "prop", label: "物件用途" }, { value: "storyboard", label: "分镜用途" }, { value: "video", label: "视频用途" },
  { value: "audio", label: "音频用途" }, { value: "other", label: "其他用途" },
];

export function AssetFolderTree({ project, selectedFolderId, selectedPurpose, onFolderChange, onPurposeChange }: {
  project: string; selectedFolderId: string | null | undefined; selectedPurpose: AssetPurpose | null;
  onFolderChange: (folderId: string | null | undefined) => void; onPurposeChange: (purpose: AssetPurpose | null) => void;
}) {
  const foldersQuery = useAssetFolders(project);
  const createFolder = useCreateAssetFolder(project);
  const renameFolder = useRenameAssetFolder(project);
  const deleteFolder = useDeleteAssetFolder(project);
  const organizeAsset = useOrganizeAsset(project);
  const folders = foldersQuery.data?.data.folders ?? [];
  const [operationError, setOperationError] = useState<string | null>(null);
  const runMutation = async (operation: () => Promise<unknown>) => {
    setOperationError(null);
    try { await operation(); return true; }
    catch (error) { setOperationError(error instanceof Error ? error.message : "请求失败，请稍后重试"); return false; }
  };
  const create = async () => { const name = window.prompt("目录名称")?.trim(); if (name) await runMutation(() => createFolder.mutateAsync({ name })); };
  const rename = async (folderId: string, currentName: string) => { const name = window.prompt("目录名称", currentName)?.trim(); if (name && name !== currentName) await runMutation(() => renameFolder.mutateAsync({ folderId, name })); };
  const remove = async (folderId: string, name: string) => { if (!window.confirm(`删除“${name}”？目录内素材将回到未归类。`)) return; const removed = await runMutation(() => deleteFolder.mutateAsync({ folderId })); if (removed && selectedFolderId === folderId) onFolderChange(null); };
  const drop = async (event: React.DragEvent, folderId: string | null) => {
    event.preventDefault(); const raw = event.dataTransfer.getData(ASSET_ORGANIZATION_DRAG_MIME); if (!raw) return;
    let payload: AssetOrganizationDragPayload; try { payload = JSON.parse(raw) as AssetOrganizationDragPayload; } catch { return; }
    await runMutation(() => organizeAsset.mutateAsync({ ...payload, folder_id: folderId }));
  };
  const rowClass = (active: boolean) => cn("flex min-h-8 w-full items-center rounded-md px-2 text-left text-xs transition-colors", active ? "bg-white/10 text-white" : "text-white/55 hover:bg-white/[0.06] hover:text-white/80");
  return <aside className="w-44 shrink-0 border-r border-white/[0.08] bg-black/20 p-2">
    <div className="mb-2 flex items-center justify-between px-1"><span className="text-[11px] font-medium tracking-[0.16em] text-white/45">素材目录</span><button type="button" onClick={() => void create()} className="text-[11px] text-white/55 hover:text-white">新建目录</button></div>
    <div className="space-y-0.5">
      <button type="button" className={rowClass(selectedFolderId === undefined)} onClick={() => onFolderChange(undefined)}>全部素材</button>
      <button type="button" data-testid="folder-drop-unfiled" className={rowClass(selectedFolderId === null)} onClick={() => onFolderChange(null)} onDragOver={(event) => event.preventDefault()} onDrop={(event) => void drop(event, null)}>未归类</button>
      {foldersQuery.isLoading ? <div className="px-2 py-2 text-[11px] text-white/40">目录加载中…</div> : foldersQuery.isError ?
        <div role="alert" className="rounded-md border border-red-500/20 bg-red-500/5 p-2 text-[11px] text-red-300"><div>目录加载失败：{foldersQuery.error instanceof Error ? foldersQuery.error.message : "请求失败"}</div><button type="button" onClick={() => void foldersQuery.refetch()} className="mt-1 text-white/70 underline hover:text-white">重试目录</button></div> :
        folders.map((folder) => <div key={folder.id} data-testid={`folder-drop-${folder.id}`} className="group flex items-center rounded-md" onDragOver={(event) => event.preventDefault()} onDrop={(event) => void drop(event, folder.id)}>
          <button type="button" className={cn(rowClass(selectedFolderId === folder.id), "min-w-0 flex-1")} onClick={() => onFolderChange(folder.id)}><span className="truncate">{folder.name}</span><span className="ml-auto pl-2 text-white/30">{folder.asset_count}</span></button>
          <button type="button" aria-label={`重命名 ${folder.name}`} onClick={() => void rename(folder.id, folder.name)} className="px-1 text-[10px] text-white/35 hover:text-white">改</button><button type="button" aria-label={`删除 ${folder.name}`} onClick={() => void remove(folder.id, folder.name)} className="px-1 text-[10px] text-white/35 hover:text-red-300">删</button>
        </div>)}
    </div>
    {operationError ? <div role="alert" className="mt-2 rounded-md border border-red-500/20 bg-red-500/5 p-2 text-[11px] text-red-300">目录操作失败：{operationError}</div> : null}
    <div className="mt-4 border-t border-white/[0.08] pt-2"><div className="mb-1 px-1 text-[11px] tracking-[0.16em] text-white/40">用途筛选</div><div className="flex flex-wrap gap-1">{PURPOSES.map((purpose) => <button key={purpose.label} type="button" className={cn("rounded border px-1.5 py-1 text-[10px]", selectedPurpose === purpose.value ? "border-white/25 bg-white/10 text-white" : "border-white/[0.08] text-white/45 hover:text-white/75")} onClick={() => onPurposeChange(purpose.value)}>{purpose.label}</button>)}</div></div>
  </aside>;
}
