import { apiCall } from './client';
import {
  listDefinitions,
  getDefinition,
  listConstraints,
  listCreativeObjects,
  type DefinitionVersion,
  type CreativeDefinition,
  type CreativeObject,
  type ConstraintRecord,
  type Scalar,
} from './creativeSettings';
import { listAgentAdoptions, type AgentConfiguration, type AgentAdoption } from './reusableAgents';
export interface TemplateRef {
  id: string;
  revision: number;
}
export interface TemplateSelection {
  name: string;
  description: string;
  definitions: { name: string; revision: number }[];
  constraints: TemplateRef[];
  agents: TemplateRef[];
  examples: TemplateRef[];
  relations: string[];
}
export interface TemplateOrigin {
  project_id: string;
  name?: string;
  revision?: number;
  id?: string;
  constraint_id?: string;
  adoption_id?: string;
  source?: { kind: string; actor_id: string };
}
export interface FrozenTemplate {
  name: string;
  description: string;
  definitions: {
    key: string;
    definition: CreativeDefinition;
    origin: TemplateOrigin;
    was_confirmed: boolean;
  }[];
  constraints: {
    key: string;
    value: Scalar;
    suggested_level: string;
    citation: string;
    origin: TemplateOrigin;
    rule_key?: string;
    rule?: { field: string; operator: string };
  }[];
  agents: { configuration: AgentConfiguration; origin: TemplateOrigin }[];
  examples: {
    key: string;
    definition_key: string;
    data: Record<string, Scalar>;
    origin: TemplateOrigin;
  }[];
  relations: {
    key: string;
    definition_key: string;
    source_key: string;
    target_key: string;
    data: Record<string, Scalar>;
    origin: TemplateOrigin;
  }[];
}
export interface PersonalTemplate extends TemplateRef {
  proof: string;
  configuration: FrozenTemplate;
}
export interface TemplateInstanceDraft {
  id: string;
  definition: { name: string; revision: number };
  data: Record<string, Scalar>;
  origin: TemplateOrigin;
  source_id?: string;
  target_id?: string;
}
export interface TemplateAdoption {
  adoption_id: string;
  revision: number;
  fixed: PersonalTemplate;
  effective: FrozenTemplate;
  overrides: Record<string, { value: unknown; base: unknown }>;
  source: { user_id: string; ref: TemplateRef };
  definition_mapping: Record<string, { name: string; revision: number }>;
  constraint_drafts: string[];
  example_drafts: TemplateInstanceDraft[];
  relation_drafts: TemplateInstanceDraft[];
  instantiated?: { objects: Record<string, string>; relations: Record<string, string> };
  previous_installations?: unknown[];
}
export interface TemplatePreview {
  configuration: FrozenTemplate;
  proof: string;
}
export interface TemplatePlan {
  ref: TemplateRef;
  proof: string;
  definition_names: Record<string, string>;
  definitions: {
    key: string;
    definition: CreativeDefinition;
    expected_revision: number;
    revision: number;
    reuse: boolean;
    active_revision: number;
  }[];
  conflicts: { source_key: string; target_name: string; existing_revision: number }[];
}
export interface TemplateUpgrade {
  adoption_id: string;
  expected_revision: number;
  before: FrozenTemplate;
  after: FrozenTemplate;
  overrides: TemplateAdoption['overrides'];
  conflicts: string[];
  ref: TemplateRef;
  proof: string;
  installation: TemplatePlan;
  existing_constraint_drafts: ConstraintRecord[];
  notice: string;
}
export interface TemplateActivation {
  adoption_id: string;
  expected_revision: number;
  definitions: DefinitionVersion[];
  examples: TemplateInstanceDraft[];
  relations: TemplateInstanceDraft[];
  proof: string;
  notice: string;
}
export interface TemplateCommand {
  request_id: string;
  action: string;
  payload: Record<string, unknown>;
}
export type TemplateChannel = 'personal' | 'project';
export type TemplateResult =
  | TemplateRef
  | {
      status: 'applied';
      adoption_id: string;
      revision: number;
      request_id: string;
      instantiated?: TemplateAdoption['instantiated'];
    };
export interface TemplateRelation {
  relation_id: string;
  source_id: string;
  target_id: string;
  definition_name: string;
  schema_revision: number;
  title: string;
  data?: Record<string, Scalar>;
}
export interface TemplateSources {
  definitions: DefinitionVersion[];
  constraints: ConstraintRecord[];
  agents: AgentAdoption[];
  objects: (CreativeObject & { data?: Record<string, Scalar> })[];
  relations: TemplateRelation[];
}
const creation = (p: string) => `projects/${encodeURIComponent(p)}/creation`;
const root = (p: string) => `${creation(p)}/reusable/template`;
export const listPersonalTemplates = (p: string) =>
  apiCall<PersonalTemplate[]>(`${root(p)}/personal`);
export const getPersonalTemplate = (p: string, ref: TemplateRef) =>
  apiCall<PersonalTemplate>(
    `${root(p)}/personal/versions/${encodeURIComponent(ref.id)}/${ref.revision}`,
  );
export const listTemplateAdoptions = (p: string) =>
  apiCall<TemplateAdoption[]>(`${root(p)}/adoptions`);
export const getTemplateAdoption = (p: string, id: string, revision: number) =>
  apiCall<TemplateAdoption>(`${root(p)}/adoptions/${encodeURIComponent(id)}?revision=${revision}`);
export const previewTemplate = (p: string, configuration: TemplateSelection) =>
  apiCall<TemplatePreview>(`${root(p)}/preview`, { method: 'POST', json: { configuration } });
export const previewTemplatePatch = (
  p: string,
  ref: TemplateRef,
  changes: Partial<Pick<FrozenTemplate, 'name' | 'description'>>,
) =>
  apiCall<TemplatePreview>(`${root(p)}/personal/patch-preview`, {
    method: 'POST',
    json: { ref, changes },
  });
export const previewTemplateAdoption = (
  p: string,
  ref: TemplateRef,
  definition_names: Record<string, string>,
) =>
  apiCall<TemplatePlan>(`${root(p)}/adoption-preview`, {
    method: 'POST',
    json: { ref, definition_names },
  });
export const previewTemplateOverride = (
  p: string,
  adoption_id: string,
  changes: { name?: string | null; description?: string | null },
) =>
  apiCall<{
    adoption_id: string;
    expected_revision: number;
    before: FrozenTemplate;
    changes: { name?: string | null; description?: string | null };
    proof: string;
  }>(`${root(p)}/override-preview`, { method: 'POST', json: { adoption_id, changes } });
export const previewTemplateUpgrade = (
  p: string,
  adoption_id: string,
  ref: TemplateRef,
  definition_names: Record<string, string>,
) =>
  apiCall<TemplateUpgrade>(`${root(p)}/upgrade-preview`, {
    method: 'POST',
    json: { adoption_id, ref, definition_names },
  });
export const previewTemplateActivation = (p: string, id: string) =>
  apiCall<TemplateActivation>(`${root(p)}/activation-preview/${encodeURIComponent(id)}`);
export const postTemplateOperation = (
  p: string,
  channel: TemplateChannel,
  request: TemplateCommand,
) =>
  apiCall<TemplateResult>(
    `${root(p)}/${channel === 'personal' ? 'personal/operations' : 'operations'}`,
    { method: 'POST', json: request },
  );
export const getTemplateReceipt = (p: string, channel: TemplateChannel, id: string) =>
  apiCall<TemplateResult | { status: 'unknown' }>(
    `${root(p)}/${channel === 'personal' ? 'personal/requests' : 'requests'}/${encodeURIComponent(id)}`,
  );
export async function listTemplateSources(project: string): Promise<TemplateSources> {
  const [index, constraints, agents, objects, relations] = await Promise.all([
    listDefinitions(project),
    listConstraints(project),
    listAgentAdoptions(project),
    listCreativeObjects(project),
    apiCall<TemplateRelation[]>(`${creation(project)}/ontology/relations`),
  ]);
  const [definitions, objectVersions, relationVersions] = await Promise.all([
    Promise.all(index.map((d) => getDefinition(project, d.name, d.current_revision))),
    Promise.all(
      objects.map((o) =>
        apiCall<{ data: Record<string, Scalar> }>(
          `${creation(project)}/ontology/objects/${encodeURIComponent(o.object_id)}?revision=${o.revision}`,
        ),
      ),
    ),
    Promise.all(
      relations.map((r) =>
        apiCall<{ payload: { data: Record<string, Scalar> } }>(
          `${creation(project)}/ontology/relations/${encodeURIComponent(r.relation_id)}`,
        ),
      ),
    ),
  ]);
  return {
    definitions,
    constraints,
    agents,
    objects: objects.map((o, i) => ({ ...o, data: objectVersions[i].data })),
    relations: relations.map((r, i) => ({ ...r, data: relationVersions[i].payload.data })),
  };
}
