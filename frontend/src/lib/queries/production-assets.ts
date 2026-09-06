// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import { queryKeys } from "@/lib/query-keys";
import type { OkResponse } from "@/types/api";

export type ProductionAssetOrigin = "generated" | "uploaded" | "legacy_import";

export type ProductionAssetAdoptionStatus =
  | "candidate"
  | "provisional"
  | "adopted"
  | "rejected"
  | "superseded";

export interface ProductionAssetQualityReport {
  passed: boolean;
  checks: Record<string, unknown>;
  issues: string[];
  style_family: string | null;
}

export interface ProductionAssetGenerationMetadata extends Record<string, unknown> {
  layout_version?: string;
  panel_layout?: string[];
  quality_report?: ProductionAssetQualityReport;
}

export interface ProductionAssetQc {
  qc_passed: boolean;
  soft_issues: string[];
  technical_error: string | null;
}

export interface ProductionAssetSlot {
  slot_id: string;
  asset_kind: string;
  critical: boolean;
  current_version_id: string | null;
  version_ids: string[];
}

export interface ProductionAssetVersion extends ProductionAssetQc {
  version_id: string;
  slot_id: string;
  source_attempt_id: string | null;
  asset_path: string;
  origin: ProductionAssetOrigin;
  generation_metadata: ProductionAssetGenerationMetadata | null;
  adoption_status: ProductionAssetAdoptionStatus;
  created_at: string | null;
}

export interface ProductionAssetSlotData {
  slot: ProductionAssetSlot;
  versions: ProductionAssetVersion[];
  current_version: ProductionAssetVersion | null;
  read_only: boolean;
  read_only_reason: string | null;
}

export interface ProductionAssetAdoptionEvent {
  slot_id: string;
  version_id: string;
  from_status: ProductionAssetAdoptionStatus;
  to_status: ProductionAssetAdoptionStatus;
  actor: string;
  at: string;
  reason: string;
  source_attempt_id: string | null;
}

export interface AdoptProductionAssetVersionData {
  slot: ProductionAssetSlot;
  versions: ProductionAssetVersion[];
  event: ProductionAssetAdoptionEvent;
}

export interface AdoptProductionAssetVersionInput {
  versionId: string;
  reason: string;
}

export function useProductionAssetSlot(
  project: string,
  slotId: string,
  assetKind: string,
  legacyAssetPath?: string,
) {
  return useQuery({
    queryKey: [
      ...queryKeys.productionAssetSlot(project, slotId),
      assetKind,
      legacyAssetPath ?? null,
    ],
    queryFn: ({ signal }) => {
      const searchParams = new URLSearchParams({ asset_kind: assetKind });
      if (legacyAssetPath) {
        searchParams.set("legacy_asset_path", legacyAssetPath);
      }
      return api
        .get(
          p`api/v1/projects/${project}/production-assets/slots/${slotId}`,
          { searchParams, signal },
        )
        .json<OkResponse<ProductionAssetSlotData>>();
    },
    enabled: Boolean(project && slotId && assetKind),
  });
}

export function useAdoptProductionAssetVersion(
  project: string,
  slotId: string,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ versionId, reason }: AdoptProductionAssetVersionInput) =>
      api
        .post(
          p`api/v1/projects/${project}/production-assets/slots/${slotId}/versions/${versionId}/adopt`,
          { json: { reason } },
        )
        .json<OkResponse<AdoptProductionAssetVersionData>>(),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: queryKeys.productionAssetSlot(project, slotId),
      }),
  });
}
