import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import type { ApiResponse, TaskResponse } from "@/types/api";

export interface SourceRange { start_line: number; end_line: number }
export interface SourceBlock {
  id: string; ordinal: number;
  kind: "scene_header" | "cast" | "action" | "speaker" | "dialogue" | "parenthetical" | "transition" | "chapter_card" | "frontmatter" | "formatting" | "unclassified";
  text: string; source_range: SourceRange;
}
export interface SemanticScene {
  id: string; ordinal: number; source_range: SourceRange; heading: string;
  location: string; time_of_day: string; characters: string[]; blocks: SourceBlock[];
  content_hash: string; status: "parsed" | "validated" | "reused" | "stale" | "failed";
}
export interface DramaticBeat {
  id: string; ordinal: number; scene_id: string; source_ranges: SourceRange[];
  characters: string[]; goal: string; obstacle: string; action: string; reaction: string;
  turn: string; result: string; emotional_shift: string; dialogue_source_ids: string[];
  estimated_duration_seconds: number; must_show: string[]; script_facts: string[];
  director_interpretation: string[]; stale: boolean; stale_reason: string | null;
}
export interface SemanticValidationIssue {
  code: string; message: string; severity: "error" | "warning";
  location: string | null; scene_id: string | null; beat_id: string | null;
  source_range: SourceRange | null;
}
export interface ScreenplaySemanticRevision {
  revision_id: string; parent_revision_id: string | null; episode: number;
  source_revision: number; source_hash: string; version: number;
  status: "draft" | "validating" | "review_required" | "active" | "superseded" | "abandoned" | "failed";
  scenes: SemanticScene[]; beats: DramaticBeat[]; metadata_blocks: SourceBlock[];
  invalidated_beat_ids: string[];
  validation_report: { passed: boolean; issues: SemanticValidationIssue[]; version: number };
  created_at: string; activated_at: string | null;
}
export interface ScreenplaySemanticCollection {
  active_revision_id: string | null;
  revisions: ScreenplaySemanticRevision[];
}
export type SemanticEditCommand =
  | { type: "split"; beat_id: string; before_line: number }
  | { type: "merge"; first_beat_id: string; second_beat_id: string }
  | { type: "reorder"; scene_id: string; beat_ids: string[] }
  | { type: "update"; beat_id: string; action?: string; goal?: string; obstacle?: string; reaction?: string; turn?: string; result?: string; emotional_shift?: string; estimated_duration_seconds?: number };

const root = (project: string, episode: number) => ["projects", project, "episodes", episode, "screenplay-semantics"] as const;
const collectionPath = (project: string, episode: number) => p`api/v1/projects/${project}/episodes/${episode}/screenplay-semantics`;
const revisionPath = (project: string, episode: number, revisionId: string) => `${collectionPath(project, episode)}/${encodeURIComponent(revisionId)}`;

function useInvalidate(project: string, episode: number) {
  const client = useQueryClient();
  return () => client.invalidateQueries({ queryKey: root(project, episode) });
}

export function useScreenplaySemantics(project: string, episode: number) {
  return useQuery({
    queryKey: root(project, episode),
    queryFn: ({ signal }) => api.get(collectionPath(project, episode), { signal }).json<ApiResponse<ScreenplaySemanticCollection>>(),
    enabled: Boolean(project) && episode > 0,
  });
}

export function useCreateScreenplaySemantics(project: string, episode: number) {
  const invalidate = useInvalidate(project, episode);
  return useMutation({
    mutationFn: (sceneIds: string[] = []) => api.post(collectionPath(project, episode), { json: { scene_ids: sceneIds, concurrency: 5 } }).json<TaskResponse>(),
    onSuccess: invalidate,
  });
}

export function useRetrySemanticScene(project: string, episode: number) {
  const invalidate = useInvalidate(project, episode);
  return useMutation({
    mutationFn: ({ revisionId, sceneId }: { revisionId: string; sceneId: string }) => api.post(`${revisionPath(project, episode, revisionId)}/scenes/${encodeURIComponent(sceneId)}/retry`, { json: {} }).json<TaskResponse>(),
    onSuccess: invalidate,
  });
}

export function useRepairScreenplaySemantics(project: string, episode: number) {
  const invalidate = useInvalidate(project, episode);
  return useMutation({
    mutationFn: ({ revisionId }: { revisionId: string }) =>
      api
        .post(`${revisionPath(project, episode, revisionId)}/repair`, {
          json: { concurrency: 3 },
        })
        .json<TaskResponse>(),
    onSuccess: invalidate,
  });
}

export function useEditScreenplaySemantics(project: string, episode: number) {
  const invalidate = useInvalidate(project, episode);
  return useMutation({
    mutationFn: ({ revisionId, command }: { revisionId: string; command: SemanticEditCommand }) => api.post(`${revisionPath(project, episode, revisionId)}/edits`, { json: command }).json<ApiResponse<ScreenplaySemanticRevision>>(),
    onSuccess: invalidate,
  });
}

export function useActivateScreenplaySemantics(project: string, episode: number) {
  const invalidate = useInvalidate(project, episode);
  return useMutation({
    mutationFn: ({ revisionId }: { revisionId: string }) => api.post(`${revisionPath(project, episode, revisionId)}/activate`, { json: {} }).json<ApiResponse<ScreenplaySemanticRevision>>(),
    onSuccess: invalidate,
  });
}

export const screenplaySemanticKeys = { all: root };
