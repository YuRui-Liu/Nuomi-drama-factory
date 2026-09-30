import { apiCall } from './client';
import type { DirectorDraft, DirectorImage, DirectorAttempt, OptimizedDirector } from '@/features/canvas/domain/canvasNodes';

export interface DirectorCapabilities {
  models: { id: string; label: string; adapter: string; referenceAdapter: string }[];
  referenceLimit: number;
  effectiveReferenceLimit: number;
  configuredReferenceLimit: number;
  fps: number;
  frameStep: number;
  frameOffset: number;
  params: { resolution: string[]; aspectRatio: string[] };
  sizes: { resolution: string; aspectRatio: string; width: number; height: number; megapixels: number }[];
  modes: { id: string; supported: boolean }[];
}

export interface TechniqueCard {
  id: string; version: string; status: 'active' | 'retired'; title: string; summary: string;
  category: string; intent: string; content_hash: string;
  applicability: { modes: ('i2v' | 'fl2v' | 'ref_only')[]; min_duration_seconds: number;
    max_duration_seconds: number; last_frame_constraint: 'required' | 'allowed' | 'forbidden' };
  sources: { url: string; credit: string; source_type: string; checked_at: string; basis: string }[];
}
export interface DirectorTechniqueCatalog { catalogVersion: string; techniques: TechniqueCard[] }

type Wire = Record<string, any>;
const path = (project: string) => `projects/${encodeURIComponent(project)}/freezone/video-director`;

export function imageToWire(image: DirectorImage | null): Wire | null {
  return image && { image_id: image.imageId, url: image.url, asset_id: image.assetId ?? null,
    character_id: image.characterId ?? null, variant_id: image.variantId ?? null,
    variant_label: image.variantLabel ?? null, asset_kind: image.assetKind ?? null, sha256: image.sha256 ?? null };
}
function imageFromWire(image: Wire | null): DirectorImage | null {
  return image && { imageId: image.image_id, url: image.url, assetId: image.asset_id,
    characterId: image.character_id, variantId: image.variant_id,
    variantLabel: image.variant_label, assetKind: image.asset_kind, sha256: image.sha256 };
}
export function draftToWire(draft: DirectorDraft): Wire {
  return { schema_version: draft.schemaVersion, revision: draft.revision, model_id: draft.modelId,
    aspect_ratio: draft.aspectRatio, resolution: draft.resolution,
    references: draft.references.map(imageToWire), segments: draft.segments.map((segment) => ({
      id: segment.id, prompt: segment.prompt, duration_seconds: segment.durationSeconds,
      first_frame: imageToWire(segment.firstFrame), last_frame: imageToWire(segment.lastFrame),
      technique: segment.technique ?? null,
    })) };
}
export function draftFromWire(draft: Wire): DirectorDraft {
  return { schemaVersion: 1, revision: draft.revision, modelId: draft.model_id,
    aspectRatio: draft.aspect_ratio, resolution: draft.resolution,
    references: (draft.references ?? []).map(imageFromWire).filter(Boolean) as DirectorImage[],
    segments: (draft.segments ?? []).map((segment: Wire) => ({ id: segment.id, prompt: segment.prompt,
      durationSeconds: segment.duration_seconds, firstFrame: imageFromWire(segment.first_frame),
      lastFrame: imageFromWire(segment.last_frame), technique: segment.technique ?? null })) };
}
function optimizedFromWire(data: Wire | null): OptimizedDirector | null {
  return data && { revision: data.revision, route: data.route, profileId: data.profile_id,
    profileVersion: data.profile_version, optimizedAt: data.optimized_at,
    segments: (data.segments ?? []).map((segment: Wire) => ({ segmentId: segment.segment_id,
      mode: segment.mode, requestedDurationSeconds: segment.requested_duration_seconds,
      durationSeconds: segment.duration_seconds, frames: segment.frames, wire: segment.wire,
      prompt: segment.prompt })) };
}
export function attemptFromWire(data: Wire): DirectorAttempt {
  return { id: data.id, projectId: data.project_id, canvasId: data.canvas_id, nodeId: data.node_id,
    requestId: data.request_id, parentAttemptId: data.parent_attempt_id ?? null,
    revision: data.revision, snapshot: draftFromWire(data.snapshot), stage: data.stage,
    optimized: optimizedFromWire(data.optimized), frozenTechniques: data.frozen_techniques ?? {}, rulesHash: data.rules_hash ?? null,
    referenceLimit: data.reference_limit ?? null, workflowId: data.workflow_id ?? null,
    workflowProfileId: data.workflow_profile_id ?? null, workflowProfileVersion: data.workflow_profile_version ?? null,
    actualParameters: data.actual_parameters ?? null, taskId: data.task_id ?? null,
    providerTaskId: data.provider_task_id ?? null, resultUrl: data.result_url ?? null,
    error: data.error ?? null, failedStage: data.failed_stage ?? null,
    createdAt: data.created_at ?? null, updatedAt: data.updated_at ?? null };
}
export async function getDirectorTechniques(project: string): Promise<DirectorTechniqueCatalog> {
  const d = await apiCall<{ catalog_version: string; techniques: TechniqueCard[] }>(`${path(project)}/techniques`);
  return { catalogVersion: d.catalog_version, techniques: d.techniques };
}
export async function getDirectorCapabilities(project: string): Promise<DirectorCapabilities> {
  const d = await apiCall<Wire>(`${path(project)}/capabilities`);
  return { models: d.models.map((m: Wire) => ({ ...m, referenceAdapter: m.reference_adapter })),
    referenceLimit: d.reference_limit, effectiveReferenceLimit: d.effective_reference_limit,
    configuredReferenceLimit: d.configured_reference_limit, fps: d.fps, frameStep: d.frame_step,
    frameOffset: d.frame_offset, params: { resolution: d.params.resolution, aspectRatio: d.params.aspect_ratio },
    sizes: d.sizes.map((s: Wire) => ({ ...s, aspectRatio: s.aspect_ratio })), modes: d.modes };
}
export async function listDirectorAttempts(project: string, canvasId: string, nodeId: string): Promise<DirectorAttempt[]> {
  const query = new URLSearchParams({ canvas_id: canvasId, node_id: nodeId });
  const d = await apiCall<{ attempts: Wire[] }>(`${path(project)}/attempts?${query}`);
  return d.attempts.map(attemptFromWire);
}
export async function getDirectorAttempt(project: string, attemptId: string): Promise<DirectorAttempt> {
  const d = await apiCall<{ attempt: Wire }>(`${path(project)}/attempts/${encodeURIComponent(attemptId)}`);
  return attemptFromWire(d.attempt);
}
async function action(project: string, suffix: string, body?: Wire): Promise<DirectorAttempt> {
  const d = await apiCall<{ attempt: Wire }>(`${path(project)}/${suffix}`, { method: 'post', ...(body ? { json: body } : {}) });
  return attemptFromWire(d.attempt);
}
export function createDirectorAttempt(project: string, canvasId: string, nodeId: string, requestId: string, draft: DirectorDraft): Promise<DirectorAttempt> {
  return action(project, 'attempts', { canvas_id: canvasId, node_id: nodeId, request_id: requestId, draft: draftToWire(draft) });
}
export function retryDirectorAttempt(project: string, attemptId: string): Promise<DirectorAttempt> {
  return action(project, `attempts/${encodeURIComponent(attemptId)}/retry`);
}
export function resumeDirectorAttempt(project: string, attemptId: string): Promise<DirectorAttempt> {
  return action(project, `attempts/${encodeURIComponent(attemptId)}/resume`);
}
