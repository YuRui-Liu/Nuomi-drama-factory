import { apiCall } from './client';
export type Scalar = string | number | boolean;
export type FieldType = 'string' | 'integer' | 'number' | 'boolean';
export interface FieldRule {
  type: FieldType;
  title?: string;
  required?: boolean;
  enum?: Scalar[];
}
export interface CreativeDefinition {
  name: string;
  title: string;
  category: 'object' | 'relation';
  fields: Record<string, FieldRule>;
  source_kind?: string;
  target_kind?: string;
}
export interface DefinitionIndex {
  name: string;
  category: 'object' | 'relation';
  current_revision: number;
  active_revision: number;
}
export interface DefinitionVersion extends DefinitionIndex {
  revision: number;
  definition: CreativeDefinition;
  source: { kind: string; actor_id: string };
  confirmations: { basis: string; actor_id: string; operation_id: string }[];
}
export type CreativeScope =
  | { level: 'project' }
  | { level: 'canvas'; canvas_id: string }
  | { level: 'group'; canvas_id: string; group_id: string }
  | { level: 'node'; canvas_id: string; node_id: string }
  | { level: 'episode'; episode_id: string }
  | { level: 'object'; object_id: string };
export interface CreativeObject {
  object_id: string;
  revision: number;
  kind: string;
  definition_name: string;
  schema_revision: number;
  scope: CreativeScope;
  title: string;
}
export interface ConstraintRecord {
  constraint_id: string;
  revision: number;
  key: string;
  value: Scalar;
  level: 'draft' | 'preference' | 'hard';
  scope: CreativeScope;
  citation: string;
  source: { kind: string; actor_id: string };
  changed_by?: { kind: string; actor_id: string };
  confirmation?: { basis: string; actor_id: string; previous_revision: number };
  scope_status?: 'valid' | 'inactive';
  scope_reason?: string;
  rule?: { kind: string; field: string; operator: 'equals'; schema_revision: number };
}
export interface ConstraintEffect {
  scope: CreativeScope;
  scope_proof: string;
  chain: CreativeScope[];
  effective: Record<string, ConstraintRecord>;
  drafts: ConstraintRecord[];
  conflicts: {
    key: string;
    hard_ref: { constraint_id: string; revision: number };
    conflicting_ref: { constraint_id: string; revision: number };
  }[];
  not_evaluated?: string;
}
export interface SettingsRequest {
  request_id: string;
  action: string;
  payload: Record<string, unknown>;
}
export type SettingsDomain = 'ontology' | 'constraints';
export interface SettingsResult {
  status: 'applied' | 'conflict' | 'unknown';
  request_id: string;
  operation_id?: string;
  revision?: number;
  name?: string;
  constraint_id?: string;
  code?: string;
  exception_proposal?: Record<string, unknown>;
}
const root = (p: string) => `projects/${encodeURIComponent(p)}/creation`;
export const listDefinitions = (p: string) => apiCall<DefinitionIndex[]>(`${root(p)}/ontology`);
export const getDefinition = (p: string, name: string, revision: number) =>
  apiCall<DefinitionVersion>(
    `${root(p)}/ontology/definitions/${encodeURIComponent(name)}?revision=${revision}`,
  );
export const listCreativeObjects = (p: string) =>
  apiCall<CreativeObject[]>(`${root(p)}/ontology/objects`);
export const listConstraints = (p: string) => apiCall<ConstraintRecord[]>(`${root(p)}/constraints`);
export const getConstraint = (p: string, id: string, revision: number) =>
  apiCall<ConstraintRecord>(
    `${root(p)}/constraints/records/${encodeURIComponent(id)}?revision=${revision}`,
  );
export const getEffectiveConstraints = (p: string, scope: CreativeScope) =>
  apiCall<ConstraintEffect>(
    `${root(p)}/constraints/effective?${new URLSearchParams(Object.entries(scope))}`,
  );
export const getConstraintProposal = (p: string, id: string) =>
  apiCall<Record<string, unknown>>(`${root(p)}/constraints/proposals/${encodeURIComponent(id)}`);
export const postSettingsOperation = (
  p: string,
  domain: SettingsDomain,
  request: SettingsRequest,
) => apiCall<SettingsResult>(`${root(p)}/${domain}/operations`, { method: 'POST', json: request });
export const getSettingsReceipt = (p: string, domain: SettingsDomain, id: string) =>
  apiCall<SettingsResult>(`${root(p)}/${domain}/requests/${encodeURIComponent(id)}`);
