// SPDX-License-Identifier: Elastic-2.0
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import { queryKeys } from "@/lib/query-keys";
import type { ApiResponse } from "@/types/api";

export type ContinuityLockViolation = "identity" | "spatial" | "prop" | "camera" | "lighting";

export interface ObservedBoundaryInput {
  segmentId: string;
  contractRevision: number;
  observedCarryOut: string;
  acceptDeviation: boolean;
  deviationReason: string;
  lockViolations: ContinuityLockViolation[];
}

export interface ContinuityEvidence {
  source: "explicit" | "director_world" | "inferred";
  confidence?: number | null;
}

export type ContinuityRiskDimensionName = "spatial" | "identity" | "motion" | "continuity";

export interface ContinuityRiskDimension {
  dimension: ContinuityRiskDimensionName;
  level: 0 | 1 | 2;
  reasons?: string[];
}

export interface ContinuityRiskReport {
  spatial: ContinuityRiskDimension;
  identity: ContinuityRiskDimension;
  motion: ContinuityRiskDimension;
  continuity: ContinuityRiskDimension;
  blockers?: string[];
}

export interface ContinuityModeDecision {
  requested?: "auto" | "i2va" | "fl2va";
  mode?: "i2va" | "fl2va" | null;
  reason_codes?: string[];
  blockers?: string[];
}

export interface ContinuityBundle {
  adapter?: "base-h3" | "h3-ref";
  mode?: "i2va" | "fl2va";
  compiler_id?: string;
  compiler_version?: number;
  diagnostics?: string[];
  bundle_sha256?: string;
}

export interface ContinuityContractBoundary {
  carry_in: string;
  planned_carry_out: string;
  observed_carry_out?: string | null;
  deviation_accepted?: boolean;
  deviation_reason?: string;
}

export interface ContinuityContract {
  revision: number;
  shot_id: string;
  predecessor_shot_id?: string | null;
  boundary: ContinuityContractBoundary;
  evidence?: ContinuityEvidence;
}

export interface ObservedBoundary {
  value: string;
  source_contract_revision: number;
  result_contract_revision: number;
  accepted?: boolean;
  deviation_reason?: string;
  lock_violations?: ContinuityLockViolation[];
}

/** Review DTO fields are optional so manifests emitted before format v2 remain readable. */
export interface ShotContinuityManifestUnit {
  segment_id?: string;
  beat_ids: string[];
  label?: string | null;
  mode: string;
  duration_seconds: number;
  director_plan?: unknown | null;
  final_prompt: string;
  prompt_profile?: unknown | null;
  quality_report?: unknown | null;
  input_summary?: unknown | null;
  first_frame_url?: string | null;
  last_frame_url?: string | null;
  workflow?: string | null;
  model?: string | null;
  provider?: string | null;
  provider_task_id?: string | null;
  continuity_contracts?: ContinuityContract[];
  risk_report?: ContinuityRiskReport | null;
  mode_decision?: ContinuityModeDecision | null;
  compiled_bundle?: ContinuityBundle | null;
  planned_carry_out?: string;
  observed_carry_out?: ObservedBoundary | null;
  [key: string]: unknown;
}

export interface ShotContinuityPromptManifest {
  format_version?: number;
  workflow_parameters?: Record<string, string>;
  provider_parameters?: Record<string, unknown>;
  actual_output?: Record<string, number>;
  units: ShotContinuityManifestUnit[];
  stale_dependent_shot_ids?: string[];
}

export function useRecordObservedBoundary(project: string, episode: number, groupId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: ObservedBoundaryInput) => api.put(
      p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/video/segments/${input.segmentId}/continuity`,
      {
        json: {
          contract_revision: input.contractRevision,
          observed_carry_out: input.observedCarryOut,
          accept_deviation: input.acceptDeviation,
          deviation_reason: input.deviationReason,
          lock_violations: input.lockViolations,
        },
      },
    ).json<ApiResponse<ShotContinuityPromptManifest>>(),
    onSuccess: () => Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.narrativeGroups(project, episode) }),
      queryClient.invalidateQueries({
        queryKey: [...queryKeys.narrativeGroups(project, episode), groupId, "video", "prompts"],
      }),
    ]),
  });
}
