import { api } from "@/lib/api";
import { p } from "@/lib/api-path";

export type RepairContext = { project: string; episode: number; revision: string; groupId?: string; bindingId: string; referenceRevision: string };
export type CharacterReferenceRepair = {
  repair_id: string; source_reference_revision: string; character_name: string;
  identity_id: string | null; candidate_id: string | null; display_label: string;
  evidence: string[]; appearance: string; costume: string; reuse_identity: boolean;
  status: "draft" | "creating" | "selected" | "bound" | "binding_failed" | "source_changed";
  error?: string | null;
  existing_choices: { character_name: string; identity_id: string | null; label: string; has_image: boolean }[];
};
export function repairErrorMessage(value: unknown) {
  const text = typeof value === "string" ? value : value instanceof Error ? value.message : "";
  const messages: Record<string, string> = {
    CHARACTER_EXISTS_SELECT_EXPLICITLY: "项目中已有同名角色，请明确选择已有角色身份，或修改新角色名称。",
    REPAIR_ALREADY_BOUND: "角色引用已绑定，请刷新引用。",
    MISSING_IMAGE_REUSES_IDENTITY: "该角色仅缺少形象，请沿用已有身份进入选角。",
    INVALID_CHARACTER_REPAIR: "该引用无法进行角色修复，请刷新引用确认。",
    REQUEST_ID_CONFLICT: "修复请求与原引用不一致，请重新打开引用确认。",
    EPISODE_NOT_FOUND: "当前剧集不存在，请返回项目检查。",
  };
  if (messages[text]) return messages[text];
  if (/source_changed|revision|stale/i.test(text)) return "规划引用已变化，请关闭后重新打开引用确认。";
  if (/identity|character.*not.*found/i.test(text)) return "角色身份已变化，请重新选择已有角色。";
  if (/candidate|adopt/i.test(text)) return "请先在选角中确认采用角色形象，再重试绑定。";
  return /[\u4e00-\u9fff]/.test(text) ? text : "角色引用处理失败，请重试。";
}
const base = (c: RepairContext) => p`api/v1/projects/${c.project}/episodes/${c.episode}/director-plans/${c.revision}/character-reference-repairs`;
export const repairStorageKey = (c: RepairContext) => `character-reference-repair:${JSON.stringify([c.project, c.episode, c.revision, c.groupId ?? null, c.bindingId, c.referenceRevision])}`;
const pendingRequests = new Map<string, { requestId: string; repairId?: string }>();
async function request(url: string, method: "get" | "post" | "patch", body?: unknown): Promise<CharacterReferenceRepair> {
  try {
    const response = await api[method](url, { ...(body ? { json: body } : {}), throwHttpErrors: false });
    const result = await response.json() as { ok?: boolean; data: CharacterReferenceRepair; detail?: { code?: string; error?: string }; error?: string };
    if (!response.ok || !result.ok) throw new Error(repairErrorMessage(result.detail?.code || result.detail?.error || result.error));
    return result.data;
  } catch (error) { throw new Error(repairErrorMessage(error)); }
}
export async function openCharacterRepair(c: RepairContext) {
  const key = repairStorageKey(c);
  let saved: { requestId: string; repairId?: string } | null = null;
  try { saved = JSON.parse(localStorage.getItem(key) || "null"); } catch { /* unavailable storage */ }
  const record = saved?.requestId ? saved : pendingRequests.get(key) ?? { requestId: crypto.randomUUID() };
  pendingRequests.set(key, record);
  try { localStorage.setItem(key, JSON.stringify(record)); } catch { /* memory session still works */ }
  const repair = record.repairId ? await getCharacterRepair(c, record.repairId) : await request(base(c), "post", {
    binding_id: c.bindingId, expected_reference_revision: c.referenceRevision, request_id: record.requestId, group_id: c.groupId,
  });
  pendingRequests.set(key, { ...record, repairId: repair.repair_id });
  try { localStorage.setItem(key, JSON.stringify({ ...record, repairId: repair.repair_id })); } catch { /* optional persistence */ }
  return repair;
}
export const getCharacterRepair = (c: RepairContext, id: string) => request(`${base(c)}/${encodeURIComponent(id)}`, "get");
export const selectCharacterRepair = (c: RepairContext, repair: CharacterReferenceRepair, fields: { mode: "existing" | "create"; character_name: string; identity_id?: string | null; appearance?: string; costume?: string }) => request(`${base(c)}/${encodeURIComponent(repair.repair_id)}`, "patch", { ...fields, expected_reference_revision: repair.source_reference_revision });
export const bindCharacterRepair = (c: RepairContext, repair: CharacterReferenceRepair) => request(`${base(c)}/${encodeURIComponent(repair.repair_id)}/bind`, "post", { expected_reference_revision: repair.source_reference_revision, candidate_id: repair.candidate_id });
