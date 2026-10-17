import { apiCall } from './client';
import {
  getAgentReceipt,
  postAgentOperation,
  type AgentConfiguration,
} from './reusableAgents';

export interface RunOwner {
  user: string;
  project: string;
  canvas: string;
  conversation: string;
}
export interface MainAgent {
  binding_revision: number;
  adoption_id: string | null;
  current_agent: {
    adoption_id: string;
    revision: number;
    effective: AgentConfiguration;
  } | null;
}
export interface RuntimeStatus {
  available: boolean;
  code: string;
  max_model_calls: number;
  default_model_calls: number;
}
export interface Delegation {
  delegation_id?: string;
  id?: string;
  parent_id?: string | null;
  contract_hash?: string;
  contract: {
    goal: string;
    parent_id?: string | null;
    expanded_scope: { nodes: { node_id: string; title?: string }[] };
    agent: { effective: AgentConfiguration };
    [key: string]: unknown;
  };
}
export interface AgentRun {
  id: string;
  revision: number;
  state: string;
  delegation_id: string;
  error?: { message: string; node_run_id?: string; status?: string };
  unmet: string[];
  max_model_calls: number;
  remaining_model_calls: number;
  outputs: {
    operation?: string;
    title?: string;
    target_node_ids?: string[];
    node_id?: string;
    member_id?: string;
    delegation_id?: string;
    origin?: string;
    before?: Record<string, unknown>;
    after?: Record<string, unknown>;
    changes?: { path: (string | number)[]; before?: unknown; after?: unknown }[];
    before_fingerprint?: string;
    after_fingerprint?: string;
    node_run_id?: string;
    status?: string;
    revision?: number;
    after_revision?: number;
    member?: { adoption_id: string; revision: number; origin: 'main' | 'adoption' };
    delegation_origin?: { parent_id?: string | null; root_delegation_id?: string };
    projection?: {
      status: 'verified_projected' | 'stale' | string;
      node_id: string;
      input_fingerprint?: string;
      result_fingerprint?: string;
      fields?: Record<string, unknown>;
    };
    artifacts?: { id?: string; title?: string; url?: string; [key: string]: unknown }[];
    object?: { payload: { title?: string; text?: string } };
    plan?: {
      title: string;
      intent: string;
      modality: 'image' | 'video' | 'audio' | '3d';
    };
    definition_ref?: { name: string; revision: number };
    execution_enabled?: boolean;
    [key: string]: unknown;
  }[];
  rounds: {
    delegation_id?: string;
    member_id?: string;
    phase?: string;
    member?: { adoption_id: string; revision: number; origin: 'main' | 'adoption' };
    usage?: {
      input_tokens?: number;
      output_tokens?: number;
      accounting?: { status: string };
    };
  }[];
}
export interface BuiltinAgent {
  title?: string;
  proof: string;
  configuration: AgentConfiguration;
  source: { kind: 'builtin'; ref: { id: string; revision: number } };
}
export interface NodeOperationCapability {
  name: string;
  title?: string | null;
  label?: string | null;
  available: boolean;
  reason: string | null;
}
export interface NodeCapability {
  node_type: string;
  category: string;
  default_operation: string;
  operations: NodeOperationCapability[];
}
export interface OrchestrationCapabilities {
  schema_version: number;
  nodes: NodeCapability[];
  runtime?: Record<string, unknown>;
}
export interface RunStep {
  kind: 'adopt' | 'main' | 'delegation' | 'start' | 'stop' | 'resume';
  request: Record<string, unknown> & { request_id: string };
  delegationId?: string;
  runId?: string;
  result?: Record<string, unknown>;
}
const root = (p: string) => `projects/${encodeURIComponent(p)}/creation`;
const query = (o: RunOwner) => ({
  canvas_id: o.canvas,
  conversation_id: o.conversation,
});
export const getMainAgent = (p: string) =>
  apiCall<MainAgent>(`${root(p)}/main-agent`);
export const getRuntime = (p: string) =>
  apiCall<RuntimeStatus>(`${root(p)}/agent-runtime`);
export const listBuiltinAgents = (p: string) =>
  apiCall<BuiltinAgent[]>(`${root(p)}/reusable/agent/builtins`);
export const getNodeCapabilities = (p: string) =>
  apiCall<OrchestrationCapabilities>(
    `projects/${encodeURIComponent(p)}/freezone/orchestration/capabilities`,
  );
export const previewDelegation = (p: string, body: Record<string, unknown>) =>
  apiCall<{ proof: string; contract: Delegation['contract'] }>(
    `${root(p)}/delegations/preview`,
    { method: 'POST', json: body },
  );
export const listDelegations = (o: RunOwner) =>
  apiCall<Delegation[]>(`${root(o.project)}/delegations`, {
    searchParams: query(o),
  });
export const listRuns = (o: RunOwner, id: string) =>
  apiCall<AgentRun[]>(
    `${root(o.project)}/delegations/${encodeURIComponent(id)}/runs`,
    { searchParams: query(o) },
  );
export function requestStep(
  o: RunOwner,
  s: RunStep,
): Promise<Record<string, unknown>> {
  if (s.kind === 'adopt')
    return postAgentOperation(
      o.project,
      'project',
      s.request as never,
    ) as unknown as Promise<Record<string, unknown>>;
  const path =
    s.kind === 'main'
      ? 'main-agent'
      : s.kind === 'delegation'
        ? 'delegations'
        : s.kind === 'start'
          ? `delegations/${encodeURIComponent(s.delegationId!)}/runs`
          : `agent-runs/${encodeURIComponent(s.runId!)}/${s.kind}`;
  return apiCall(`${root(o.project)}/${path}`, {
    method: 'POST',
    json: s.request,
    searchParams: query(o),
  });
}
export async function receiptStep(
  o: RunOwner,
  s: RunStep,
): Promise<Record<string, unknown> | null> {
  if (s.kind === 'adopt') {
    const value = await getAgentReceipt(
      o.project,
      'project',
      s.request.request_id,
    );
    return 'status' in value && value.status === 'unknown'
      ? null
      : (value as unknown as Record<string, unknown>);
  }
  const path =
    s.kind === 'main'
      ? 'main-agent/requests'
      : s.kind === 'delegation'
        ? 'delegation-requests'
        : 'agent-run-requests';
  try {
    return await apiCall(
      `${root(o.project)}/${path}/${encodeURIComponent(s.request.request_id)}`,
      { searchParams: query(o) },
    );
  } catch (error) {
    if ((error as { status?: number }).status === 404) return null;
    throw error;
  }
}
