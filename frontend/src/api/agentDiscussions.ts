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

export interface AgentDiscussionMessage {
  request_id: string;
  role: "user" | "assistant";
  status: AgentDiscussionStatus;
  message: string;
  created_at: string;
}

export interface AgentDiscussionHistory {
  availability: AgentDiscussionAvailability;
  messages: AgentDiscussionMessage[];
}

export interface AgentDiscussionRequest {
  request_id: string;
  canvas_id: string;
  conversation_id: string;
  message: string;
}

export interface AgentDiscussionReceipt {
  request_id: string;
  status: AgentDiscussionStatus;
  created_at: string;
  updated_at: string;
  message?: string;
}

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
