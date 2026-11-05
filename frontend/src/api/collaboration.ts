// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { apiCall } from "@/api/client";
import type { AgentDiscussionSpeaker } from "./agentDiscussions";

export interface DiscussionMember extends Omit<AgentDiscussionSpeaker, "adoption_id" | "adoption_revision"> {
  adoption_id: string;
  adoption_revision: number;
  active: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface DiscussionMembers {
  main: AgentDiscussionSpeaker | null;
  members: DiscussionMember[];
}

export interface CollaborationTask {
  id: string;
  revision: number;
  state: string;
  source_request_id: string;
  canvas_id: string;
  conversation_id: string;
  user_message: string;
  intent: "prepare" | "execute_request" | string;
  speaker: AgentDiscussionSpeaker;
  goal: string;
  acceptance: unknown[];
  requested_action?: Record<string, unknown> | null;
  execution_authorization?: Record<string, unknown> | null;
  max_model_calls: number;
  scope: {
    kind: string;
    canvas_id: string;
    node_ids: string[];
    observed_canvas_revision?: number;
  };
  delegation_id?: string | null;
  run_id?: string | null;
  run?: Record<string, unknown> | null;
  prepared_execution_candidates?: PreparedExecutionCandidate[];
  continuations?: CollaborationContinuation[];
  error_code?: string | null;
  resume_available?: boolean;
  resume_blocked_reason?: string | null;
  created_at: string;
  updated_at: string;
}

export interface PreparedExecutionCandidate {
  status: "ready_to_authorize" | "authorized" | "stale" | "blocked" | string;
  code?: string | null;
  candidate: {
    canvas_id: string;
    node_id: string;
    node_type: "imageGenNode";
    operation: "generate";
    max_node_runs: 1;
    source_operation_id: string;
    request_id: string;
  };
  effect: { operation: "node.run"; node_type: "imageGenNode"; node_operation: "generate"; max_node_runs: 1 };
  source: { run_id: string; delegation_id: string; output_id: string; operation: string };
  continuation_id?: string | null;
}

export interface CollaborationContinuation {
  id: string;
  state: string;
  candidate: PreparedExecutionCandidate["candidate"];
  source: Record<string, unknown>;
  authorization_request_id?: string | null;
  delegation_id?: string | null;
  run_request_id?: string | null;
  run_id?: string | null;
  run?: Record<string, unknown> | null;
  error_code?: string | null;
}

export interface CollaborationScope {
  canvas_id: string;
  conversation_id: string;
}

const creationRoot = (project: string) =>
  `projects/${encodeURIComponent(project)}/creation`;
const freezoneRoot = (project: string) =>
  `projects/${encodeURIComponent(project)}/freezone/orchestration`;

export function getDiscussionMembers(project: string, scope: CollaborationScope) {
  return apiCall<DiscussionMembers>(`${creationRoot(project)}/discussion-members`, {
    searchParams: { canvas_id: scope.canvas_id, conversation_id: scope.conversation_id },
  });
}

export function joinDiscussionMember(
  project: string,
  scope: CollaborationScope,
  adoptionId: string,
  adoptionRevision: number,
) {
  return apiCall<{ member: DiscussionMember }>(`${creationRoot(project)}/discussion-members`, {
    method: "POST",
    json: {
      ...scope,
      adoption_id: adoptionId,
      adoption_revision: adoptionRevision,
    },
  });
}

export function leaveDiscussionMember(
  project: string,
  scope: CollaborationScope,
  adoptionId: string,
) {
  return apiCall<{ member: DiscussionMember }>(
    `${creationRoot(project)}/discussion-members/${encodeURIComponent(adoptionId)}`,
    { method: "DELETE", searchParams: { canvas_id: scope.canvas_id, conversation_id: scope.conversation_id } },
  );
}

export function listCollaborationTasks(project: string, scope: CollaborationScope) {
  return apiCall<CollaborationTask[]>(`${creationRoot(project)}/collaboration-tasks`, {
    searchParams: { canvas_id: scope.canvas_id, conversation_id: scope.conversation_id },
  });
}

export function getCollaborationTask(
  project: string,
  scope: CollaborationScope,
  taskId: string,
) {
  return apiCall<CollaborationTask>(
    `${creationRoot(project)}/collaboration-tasks/${encodeURIComponent(taskId)}`,
    { searchParams: { canvas_id: scope.canvas_id, conversation_id: scope.conversation_id } },
  );
}

export function authorizeCollaborationTask(
  project: string,
  scope: CollaborationScope,
  taskId: string,
  requestId: string,
  expectedRevision: number,
) {
  return apiCall<CollaborationTask>(
    `${creationRoot(project)}/collaboration-tasks/${encodeURIComponent(taskId)}/authorize`,
    {
      method: "POST",
      searchParams: { canvas_id: scope.canvas_id, conversation_id: scope.conversation_id },
      json: { request_id: requestId, expected_revision: expectedRevision, confirmed: true },
      retry: 0,
    },
  );
}

export function authorizePreparedExecutionCandidate(
  project: string,
  scope: CollaborationScope,
  taskId: string,
  body: { request_id: string; expected_revision: number; source_operation_id: string; source_request_id: string },
) {
  return apiCall<CollaborationTask>(
    `${creationRoot(project)}/collaboration-tasks/${encodeURIComponent(taskId)}/authorize-candidate`,
    {
      method: "POST",
      searchParams: { canvas_id: scope.canvas_id, conversation_id: scope.conversation_id },
      json: { ...body, confirmed: true },
      retry: 0,
    },
  );
}

export function controlCollaborationTask(
  project: string,
  scope: CollaborationScope,
  taskId: string,
  action: "stop" | "resume",
  requestId: string,
  expectedRevision: number,
  continuationId?: string,
) {
  return apiCall<CollaborationTask>(
    `${creationRoot(project)}/collaboration-tasks/${encodeURIComponent(taskId)}/${action}`,
    {
      method: "POST",
      searchParams: { canvas_id: scope.canvas_id, conversation_id: scope.conversation_id },
      json: { request_id: requestId, expected_revision: expectedRevision,
        ...(continuationId ? { continuation_id: continuationId } : {}) },
      retry: 0,
    },
  );
}

export type AssetDesignReference =
  | { kind: "canvas_node"; node_id: string }
  | {
      kind: "asset";
      asset_ref: { base_asset_id: string; variant_id?: string | null; revision: number };
      slot_id: string;
      role: string;
    };

export interface AssetDesignCameraSelection {
  cameraBodyId?: "panavision_dxl2" | "arri_alexa_65" | "red_vraptor_xl" | "sony_venice_2";
  lensId?: "arri_signature_prime" | "cooke_s4i" | "zeiss_supreme_prime" | "panavision_primo_70";
  focalLengthMm?: 8 | 14 | 24 | 35 | 50 | 75 | 125;
  aperture?: "f/1.4" | "f/2" | "f/2.8" | "f/4" | "f/5.6" | "f/8";
}

export interface PrepareAssetDesignRequest {
  request_id: string;
  canvas_id: string;
  expected_canvas_revision: number;
  kind: "character" | "scene";
  purpose?: "avatar" | "half_body" | "full_body" | "body" | "character_sheet" | "scene";
  aspect_ratio?: "1:1" | "9:16" | "16:9" | "3:4" | "4:3" | "3:2" | "2:3" | "4:5" | "5:4" | "21:9";
  camera_selection?: AssetDesignCameraSelection;
  name: string;
  description: string;
  prompt: string;
  source_proposal: { title: string; text: string };
  style_template_id?: string | null;
  base_asset_ref?: { base_asset_id: string; variant_id?: string | null; revision: number };
  references?: AssetDesignReference[];
  group_id?: string | null;
}

export interface PreparedAssetDesign {
  request_id: string;
  operation_id: string;
  design_ref: { id: string; revision: number; kind: string };
  source_proposal_ref: { id: string; revision: number; kind: string };
  group_id: string;
  text_node_id: string;
  image_node_id: string;
  canvas_revision: number;
  projection_status: "applied" | "pending" | "conflict";
  prepared_execution: {
    canvas_id: string;
    node_id: string;
    node_type: "imageGenNode";
    operation: "generate";
    max_node_runs: 1;
    source_operation_id: string;
    request_id: string;
  };
}

export function prepareAssetDesign(project: string, request: PrepareAssetDesignRequest) {
  return apiCall<PreparedAssetDesign>(
    `${creationRoot(project)}/asset-designs/prepare`,
    { method: "POST", json: request, retry: 0 },
  );
}

export function getAssetDesignVersion(project: string, designId: string, revision: number) {
  return apiCall<Record<string, unknown>>(
    `${creationRoot(project)}/asset-designs/${encodeURIComponent(designId)}`,
    { searchParams: { revision } },
  );
}

export interface CanvasOrchestrationRun {
  id: string;
  request_id: string;
  canvas_id: string;
  target: string;
  status: string;
  revision?: number;
  attempts: Record<string, Array<Record<string, unknown>>>;
  [key: string]: unknown;
}

export function startAssetDesignGeneration(
  project: string,
  request: { canvas_id: string; target: string; request_id: string; expected_revision: number },
) {
  return apiCall<CanvasOrchestrationRun>(`${freezoneRoot(project)}/runs`, {
    method: "POST",
    json: { ...request, operation: "generate" },
    retry: 0,
  });
}

export function listCanvasOrchestrationRuns(project: string, canvasId: string) {
  return apiCall<CanvasOrchestrationRun[]>(
    `${freezoneRoot(project)}/canvases/${encodeURIComponent(canvasId)}/runs`,
  );
}

export function getCanvasOrchestrationRun(project: string, runId: string) {
  return apiCall<CanvasOrchestrationRun>(
    `${freezoneRoot(project)}/runs/${encodeURIComponent(runId)}`,
  );
}

export function controlCanvasOrchestrationRun(
  project: string,
  runId: string,
  action: "cancel" | "resume" | "retry",
  requestId?: string,
) {
  return apiCall<CanvasOrchestrationRun>(
    `${freezoneRoot(project)}/runs/${encodeURIComponent(runId)}/${action}`,
    {
      method: "POST",
      ...(action === "retry" ? { json: { request_id: requestId ?? crypto.randomUUID() } } : {}),
      retry: 0,
    },
  );
}
