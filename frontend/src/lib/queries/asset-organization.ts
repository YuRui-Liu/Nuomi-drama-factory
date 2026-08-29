// SPDX-License-Identifier: Elastic-2.0
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { jsonWithBackendError } from "@/lib/api-errors";
import { p } from "@/lib/api-path";

export type AssetPurpose = "character" | "scene" | "prop" | "storyboard" | "video" | "audio" | "other";
export interface AssetFolder { id: string; name: string; asset_count: number; created_at?: string; updated_at?: string; }
export interface AssetPlacement { asset_key?: string; asset_type: string; asset_id: string; folder_id: string | null; purpose: AssetPurpose; created_at?: string; updated_at?: string; }
type Ok<T> = { ok: true; data: T };
type ApiError = { ok?: false; error?: string };

const keys = {
  folders: (project: string) => ["projects", project, "asset-folders"] as const,
  organization: (project: string) => ["projects", project, "asset-organization"] as const,
};

async function requireOk<T>(promise: Promise<Response>): Promise<Ok<T>> {
  const response = await jsonWithBackendError<Ok<T> | ApiError>(promise);
  if (!response.ok) throw new Error(response.error ?? "Asset organization request failed");
  return response;
}

export function useAssetFolders(project: string) {
  return useQuery({
    queryKey: keys.folders(project),
    queryFn: ({ signal }) => requireOk<{ folders: AssetFolder[] }>(api.get(p`api/v1/projects/${project}/asset-folders`, { signal, throwHttpErrors: false })),
    enabled: !!project,
  });
}

export function useAssetOrganization(project: string, filters: { folderId?: string | null; purpose?: AssetPurpose | null } = {}) {
  return useQuery({
    queryKey: [...keys.organization(project), filters.folderId === undefined ? "all" : filters.folderId ?? "unfiled", filters.purpose ?? "all"],
    queryFn: ({ signal }) => {
      const searchParams = new URLSearchParams();
      if (filters.folderId === null) searchParams.set("unfiled", "true");
      else if (filters.folderId !== undefined) searchParams.set("folder_id", filters.folderId);
      if (filters.purpose) searchParams.set("purpose", filters.purpose);
      const suffix = searchParams.size > 0 ? `?${searchParams.toString()}` : "";
      return requireOk<{ placements: AssetPlacement[] }>(api.get(`api/v1/projects/${encodeURIComponent(project)}/asset-organization${suffix}`, { signal, throwHttpErrors: false }));
    },
    enabled: !!project,
  });
}

function useInvalidation(project: string) {
  const queryClient = useQueryClient();
  return () => Promise.all([
    queryClient.invalidateQueries({ queryKey: keys.folders(project) }),
    queryClient.invalidateQueries({ queryKey: keys.organization(project) }),
  ]);
}

export function useCreateAssetFolder(project: string) {
  const invalidate = useInvalidation(project);
  return useMutation({ mutationFn: ({ name }: { name: string }) => requireOk<AssetFolder>(api.post(p`api/v1/projects/${project}/asset-folders`, { json: { name }, throwHttpErrors: false })), onSuccess: invalidate });
}
export function useRenameAssetFolder(project: string) {
  const invalidate = useInvalidation(project);
  return useMutation({ mutationFn: ({ folderId, name }: { folderId: string; name: string }) => requireOk<AssetFolder>(api.patch(p`api/v1/projects/${project}/asset-folders/${folderId}`, { json: { name }, throwHttpErrors: false })), onSuccess: invalidate });
}
export function useDeleteAssetFolder(project: string) {
  const invalidate = useInvalidation(project);
  return useMutation({ mutationFn: ({ folderId }: { folderId: string }) => requireOk<{ id: string; unfiled_count: number }>(api.delete(p`api/v1/projects/${project}/asset-folders/${folderId}`, { throwHttpErrors: false })), onSuccess: invalidate });
}
export function useOrganizeAsset(project: string) {
  const invalidate = useInvalidation(project);
  return useMutation({
    mutationFn: ({ assetType, assetId, folder_id, purpose }: { assetType: string; assetId: string; folder_id: string | null; purpose: AssetPurpose }) => requireOk<AssetPlacement>(api.put(p`api/v1/projects/${project}/assets/${assetType}/${assetId}/organization`, { json: { folder_id, purpose }, throwHttpErrors: false })),
    onSuccess: invalidate,
  });
}
