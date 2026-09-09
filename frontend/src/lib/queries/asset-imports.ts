import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";
import type { AssetImportDisposition, AssetImportPreview, AssetImportResult, AssetImportType } from "@/types/asset-import";

export function assetImportDispositionKey(disposition: AssetImportDisposition) {
  return disposition;
}

export function assetImportResultToastValues(result: AssetImportResult) {
  return {
    created: result.created_count ?? 0,
    supplemented: result.supplemented_count ?? 0,
    skipped: result.skipped_count ?? 0,
    warnings: result.warning_count ?? 0,
  };
}

export function assetImportPreviewPath(project: string, assetType: AssetImportType) {
  return `api/v1/projects/${encodeURIComponent(project)}/asset-imports/${assetType}/preview`;
}

export function assetImportConfirmPath(project: string, assetType: AssetImportType, importId: string) {
  return `api/v1/projects/${encodeURIComponent(project)}/asset-imports/${assetType}/${encodeURIComponent(importId)}/confirm`;
}

export async function previewAssetImport(project: string, assetType: AssetImportType, file: File) {
  const body = new FormData();
  body.append("file", file);
  return api
    .post(assetImportPreviewPath(project, assetType), { body, timeout: 120_000 })
    .json<AssetImportPreview>();
}

export async function confirmAssetImport(project: string, assetType: AssetImportType, importId: string) {
  return api
    .post(assetImportConfirmPath(project, assetType, importId))
    .json<AssetImportResult>();
}

export function usePreviewAssetImport(project: string, assetType: AssetImportType) {
  return useMutation({
    mutationFn: (file: File) => previewAssetImport(project, assetType, file),
  });
}

export function useConfirmAssetImport(project: string, assetType: AssetImportType) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (importId: string) => confirmAssetImport(project, assetType, importId),
    onSuccess: () => {
      const key = assetType === "character"
        ? queryKeys.characters(project)
        : assetType === "scene"
          ? queryKeys.scenes(project)
          : queryKeys.props(project);
      void queryClient.invalidateQueries({ queryKey: key });
    },
  });
}
