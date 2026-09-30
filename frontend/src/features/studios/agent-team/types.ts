export interface ResourceRef { id: string; revision: number }
export interface ResourceVersion extends ResourceRef { kind: 'prompt' | 'skill' | 'reference'; owner: string; content: string; content_hash: string; archived: boolean }
export interface TaskRoute { runtime: 'codex' | 'model_api' | 'workbuddy' | 'deepseek_harness'; model: string; reasoning_effort?: string | null; skill_id?: string | null; skill_version?: string | null; fallback?: 'stop' }
export interface MethodConfig { model: 'project' | TaskRoute; prompt: string; skills: ResourceRef[]; references: ResourceRef[]; director_preferences: Record<string, string> }
export type Overrides = Record<string, Record<string, Partial<MethodConfig>>>;
export interface DraftData { template_id: string; template_revision: number; overrides: Overrides }
export interface TeamTemplate { id: string; revision: number; name: string; owner: string; roles: Record<string, Record<string, MethodConfig>> }
export interface StoredData extends DraftData { template: TeamTemplate; resources?: ResourceVersion[] }
export interface TeamDraft { draft_revision: number; data: StoredData }
export interface TeamBinding { active_revision: number; snapshot: StoredData; created_at?: string }
export interface TeamRole { id: string; name: string; subtasks: string[]; connected: boolean; trial_supported?: Record<string, boolean> }
export interface TeamOverview { catalog: TeamRole[]; template: TeamTemplate; draft: TeamDraft | null; active: TeamBinding | null; effective: Record<string, Record<string, { config: MethodConfig; origins: Record<keyof MethodConfig, 'template' | 'project'>; trial_supported?: boolean }>>; connectivity: Record<string, Record<string, boolean>> }
export interface TeamDiff { role_id: string; subtask_id: string; field: string; before: unknown; after: unknown }
export const defaultMethod: MethodConfig = { model: 'project', prompt: '', skills: [], references: [], director_preferences: {} };
export function draftData(overview: TeamOverview): DraftData { return { template_id: overview.template.id, template_revision: overview.template.revision, overrides: structuredClone(overview.draft?.data.overrides ?? {}) }; }
export function resolveMethod(template: TeamTemplate, data: DraftData, role: string, task: string): MethodConfig { return { ...defaultMethod, ...template.roles[role]?.[task], ...data.overrides[role]?.[task] }; }
