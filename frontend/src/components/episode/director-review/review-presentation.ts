export function versionNumbers(revisions: ReadonlyArray<{ revision_id: string; created_at: string }>): Record<string, number> {
  return Object.fromEntries([...revisions].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.revision_id.localeCompare(b.revision_id)).map((revision, index) => [revision.revision_id, index + 1]));
}

export function unresolvedIssues<T extends { status: string }>(feedback: { issues: T[] } | null | undefined): T[] | undefined {
  return feedback?.issues.filter((item) => item.status !== "resolved");
}

export function reviewStatusLabel(status: string | undefined): string {
  if (status === "unavailable") return "本轮审核或修订未完成";
  return ({ passed: "创作复查通过", needs_revision: "创作复查需修改", needs_human: "需要人工判断", legacy_unreviewed: "旧版本：尚未按创作标准审查", unreviewed: "等待独立 QC" } as Record<string, string>)[status ?? "unreviewed"] ?? status ?? "等待独立 QC";
}

export function assetKindLabel(kind: string): string {
  return ({ character_identity: "人物形象", character_state: "人物状态", scene_base: "场景", scene_state: "场景状态", prop: "道具" } as Record<string, string>)[kind] ?? kind;
}
