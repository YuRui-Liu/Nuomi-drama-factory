// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { apiCall } from "@/api/client";

export type AgentDiscussionStatus =
  | "pending"
  | "completed"
  | "submission_unknown";

export interface AgentDiscussionAvailability {
  available: boolean;
  code: string;
}

/** Safe, structured error metadata persisted for an uncertain discussion request. */
export interface AgentDiscussionDiagnostic {
  outcome: string;
  code: string;
  type: string;
  http_status: number | null;
  response_received: boolean;
}

export interface AgentDiscussionMessage {
  request_id: string;
  role: "user" | "assistant";
  status: AgentDiscussionStatus;
  message: string;
  created_at: string;
  /** Identity frozen by the server when this message was accepted. Null on legacy history. */
  speaker?: AgentDiscussionSpeaker | null;
  speaker_recorded?: boolean;
  diagnostic?: AgentDiscussionDiagnostic | null;
}

export interface AgentDiscussionSpeaker {
  kind: string;
  binding_revision: number;
  adoption_id: string | null;
  adoption_revision: number | null;
  profile: Record<string, unknown>;
}

export type AgentDiscussionSelection =
  | { kind: "canvas" }
  | { kind: "nodes"; node_ids: string[] }
  | { kind: "group"; group_id: string };

export interface AgentDiscussionHistory {
  availability: AgentDiscussionAvailability;
  messages: AgentDiscussionMessage[];
}

export interface AgentDiscussionRequest {
  request_id: string;
  canvas_id: string;
  conversation_id: string;
  message: string;
  target_adoption_id?: string;
  selection?: AgentDiscussionSelection;
}

export interface AgentDiscussionReceipt {
  request_id: string;
  status: AgentDiscussionStatus;
  created_at: string;
  updated_at: string;
  message?: string;
  speaker?: AgentDiscussionSpeaker | null;
  speaker_recorded?: boolean;
  collaboration_task?: CollaborationTask | null;
  diagnostic?: AgentDiscussionDiagnostic | null;
}

/** Imported as a type only to keep the discussion wire contract discoverable here. */
import type { CollaborationTask } from "./collaboration";

export interface AgentDiscussionScope {
  canvas_id: string;
  conversation_id: string;
}

const discussionsPath = (project: string) =>
  `projects/${encodeURIComponent(project)}/creation/discussions`;

export function getAgentDiscussion(
  project: string,
  scope: AgentDiscussionScope,
): Promise<AgentDiscussionHistory> {
  return apiCall<AgentDiscussionHistory>(discussionsPath(project), {
    searchParams: { ...scope },
  });
}

export function postAgentDiscussion(
  project: string,
  request: AgentDiscussionRequest,
): Promise<AgentDiscussionReceipt> {
  return apiCall<AgentDiscussionReceipt>(discussionsPath(project), {
    method: "post",
    json: request,
    retry: 0,
  });
}
