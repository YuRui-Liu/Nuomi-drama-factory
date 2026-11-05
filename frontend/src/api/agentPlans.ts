import { apiCall } from '@/api/client';
import type { AgentDiscussionSelection, AgentDiscussionScope } from './agentDiscussions';

export interface AgentPlanTask {
  task_key: string;
  goal: string;
  acceptance: string[];
  role: { adoption_id: string; revision: number; label: string };
  skills: { id: string; documents: { path: string; sha256: string }[] }[];
  skill_manifest: { id: string; documents: { path: string; sha256: string }[] }[];
  depends_on: string[];
  outputs: { type: string; target: string }[];
  operation: string;
  permission: string;
}

export interface AgentPlan {
  id: string;
  revision: number;
  state: 'draft' | 'confirmed';
  run_id?: string | null;
  message: string;
  scope: {
    kind: string;
    canvas_id: string;
    node_ids: string[];
    observed_canvas_revision: number;
  };
  frozen_inputs: {
    kind: string;
    canvas_id: string;
    node_id: string;
    canvas_revision: number;
    input_fingerprint: string;
  }[];
  tasks: AgentPlanTask[];
  required_permission: string;
}

const root = (project: string) => `projects/${encodeURIComponent(project)}/creation/agent-plans`;

export function previewAgentPlan(project: string, request: {
  request_id: string;
  canvas_id: string;
  conversation_id: string;
  message: string;
  selection: AgentDiscussionSelection;
}) {
  return apiCall<AgentPlan>(`${root(project)}/preview`, { method: 'post', json: request, retry: 0 });
}

export function editAgentPlan(project: string, identifier: string, scope: AgentDiscussionScope,
                              body: { request_id: string; expected_revision: number; tasks: AgentPlanTask[] }) {
  return apiCall<AgentPlan>(`${root(project)}/${encodeURIComponent(identifier)}`, {
    method: 'put', searchParams: { canvas_id: scope.canvas_id, conversation_id: scope.conversation_id }, json: body, retry: 0,
  });
}

export function confirmAgentPlan(project: string, identifier: string, scope: AgentDiscussionScope,
                                 body: { request_id: string; expected_revision: number;
                                   confirmed: true; permission: 'text' }) {
  return apiCall<AgentPlan>(`${root(project)}/${encodeURIComponent(identifier)}/confirm`, {
    method: 'post', searchParams: { canvas_id: scope.canvas_id, conversation_id: scope.conversation_id }, json: body, retry: 0,
  });
}
