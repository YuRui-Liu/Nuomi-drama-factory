import { apiCall } from './client';
import type { DirectionKind } from './directions';
import type { StudioKind } from '@/features/studios/studio-api';
export interface AgentRef {
  id: string;
  revision: number;
}
export interface ResourceSource {
  kind: string;
  project_id?: string;
}
export interface AgentDefault {
  id: string;
  proof: string;
  title?: string;
  version?: string;
  source?: ResourceSource;
  configuration?: Record<string, unknown>;
}
export interface StudioRef {
  kind: StudioKind;
  id: string;
  revision: number;
}
export interface FrozenStudio {
  ref: StudioRef;
  name: string;
  configuration: Record<string, string>;
  excluded_fields?: string[];
  source?: ResourceSource;
}
export interface RequiredNodeOperation {
  node_type: string;
  operation: string;
}
export interface AgentExample {
  source_url: string;
  prompt_public: boolean;
  inputs_public: boolean;
  workflow_public: boolean;
}
export interface AgentConfiguration {
  name: string;
  responsibilities: string;
  methods: string;
  references: string[];
  skill_notes: string[];
  capabilities: string[];
  defaults: Partial<Record<DirectionKind, AgentDefault>>;
  studio_refs: (StudioRef | FrozenStudio)[];
  delivery_requirements?: string[];
  collaboration_rules?: string[];
  required_node_operations?: RequiredNodeOperation[];
  examples?: AgentExample[];
}
export type AgentChanges = Partial<{
  [K in keyof AgentConfiguration]: AgentConfiguration[K] | null;
}>;
export interface PersonalAgent extends AgentRef {
  kind: string;
  proof: string;
  configuration: AgentConfiguration;
}
export interface AgentAdoption {
  adoption_id: string;
  revision: number;
  fixed: PersonalAgent;
  overrides: Record<string, { value: unknown; base: unknown }>;
  effective: AgentConfiguration;
  source:
    | { kind?: 'personal'; user_id: string; ref: AgentRef }
    | { kind: 'builtin'; ref: AgentRef; content_hash: string };
}
export interface AgentPreview {
  configuration: AgentConfiguration;
  proof: string;
}
export interface OverridePreview {
  adoption_id: string;
  expected_revision: number;
  changes: AgentChanges;
  before: AgentConfiguration;
  proof: string;
}
export interface UpgradePreview {
  adoption_id: string;
  expected_revision: number;
  before: AgentConfiguration;
  after: AgentConfiguration;
  overrides: Record<string, { value: unknown; base: unknown }>;
  conflicts: string[];
  dependencies_before: Record<string, unknown>;
  dependencies_after: Record<string, unknown>;
  ref: AgentRef;
  proof: string;
}
export interface AgentCommand {
  request_id: string;
  action: string;
  payload: Record<string, unknown>;
}
export interface ProjectAgentResult {
  status: 'applied';
  operation_id: string;
  request_id: string;
  adoption_id: string;
  revision: number;
  effective: AgentConfiguration;
}
export type AgentResult = AgentRef | ProjectAgentResult;
export type AgentChannel = 'personal' | 'project';
const root = (project: string) => `projects/${encodeURIComponent(project)}/creation/reusable/agent`;
export const listPersonalAgents = (p: string) => apiCall<PersonalAgent[]>(`${root(p)}/personal`);
export const listAgentAdoptions = (p: string) => apiCall<AgentAdoption[]>(`${root(p)}/adoptions`);
export const getAgentCapabilities = (p: string) =>
  apiCall<{
    items: { id: string; title: string; runtime_permission: string }[];
    execution_enabled: false;
  }>(`projects/${encodeURIComponent(p)}/creation/reusable/capabilities`);
export const previewAgent = (p: string, configuration: AgentConfiguration) =>
  apiCall<AgentPreview>(`${root(p)}/preview`, { method: 'POST', json: { configuration } });
export const previewAgentPatch = (p: string, ref: AgentRef, changes: AgentChanges) =>
  apiCall<AgentPreview>(`${root(p)}/personal/patch-preview`, {
    method: 'POST',
    json: { ref, changes },
  });
export const previewAgentOverride = (p: string, adoption_id: string, changes: AgentChanges) =>
  apiCall<OverridePreview>(`${root(p)}/override-preview`, {
    method: 'POST',
    json: { adoption_id, changes },
  });
export const previewAgentUpgrade = (p: string, adoption_id: string, ref: AgentRef) =>
  apiCall<UpgradePreview>(`${root(p)}/upgrade-preview`, {
    method: 'POST',
    json: { adoption_id, ref },
  });
export const postAgentOperation = (p: string, channel: AgentChannel, request: AgentCommand) =>
  apiCall<AgentResult>(`${root(p)}/${channel === 'personal' ? 'personal/' : ''}operations`, {
    method: 'POST',
    json: request,
  });
export const getAgentReceipt = (p: string, channel: AgentChannel, id: string) =>
  apiCall<AgentResult | { status: 'unknown' }>(
    `${root(p)}/${channel === 'personal' ? 'personal/' : ''}requests/${encodeURIComponent(id)}`,
  );
export const listAgentStudioResources = (p: string) =>
  apiCall<
    (StudioRef & {
      name: string;
      portable: boolean;
      reason: string | null;
      excluded_fields: string[];
      source: ResourceSource;
    })[]
  >(`projects/${encodeURIComponent(p)}/creation/reusable/studio-resources`);
