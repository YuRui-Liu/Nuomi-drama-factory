// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { AlertTriangle, Check, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  useAdoptProductionAssetVersion,
  useProductionAssetSlot,
  type ProductionAssetVersion,
} from "@/lib/queries/production-assets";
import { resolveMediaUrl } from "@/lib/media-url";

interface CharacterStateVersionsProps {
  project: string;
  characterName: string;
  identityId: string;
  legacyAssetPath?: string | null;
}

const PANEL_LABELS: Record<string, string> = {
  front: "正面",
  side: "侧面",
  back: "背面",
};

function assetMediaUrl(project: string, assetPath: string): string {
  const encodedPath = assetPath
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
  return (
    resolveMediaUrl(
      `/api/v1/projects/${encodeURIComponent(project)}/media/${encodedPath}`,
    ) ?? ""
  );
}

function panelLayout(version: ProductionAssetVersion): string[] {
  const panels = version.generation_metadata?.panel_layout;
  return Array.isArray(panels) && panels.length > 0
    ? panels.filter((panel): panel is string => typeof panel === "string")
    : ["front", "side", "back"];
}

function statusLabel(version: ProductionAssetVersion, isCurrent: boolean): string {
  if (isCurrent) return "当前采用";
  if (!version.qc_passed) return "QC 未通过";
  if (version.adoption_status === "candidate") return "候选版本";
  if (version.adoption_status === "superseded") return "历史版本";
  return version.adoption_status;
}

export function CharacterStateVersions({
  project,
  characterName,
  identityId,
  legacyAssetPath,
}: CharacterStateVersionsProps) {
  const slotId = `character:${characterName}:state:${identityId}`;
  const slotQuery = useProductionAssetSlot(
    project,
    slotId,
    "character_state",
    legacyAssetPath ?? undefined,
  );
  const adoptVersion = useAdoptProductionAssetVersion(project, slotId);
  const payload = slotQuery.data?.ok ? slotQuery.data.data : undefined;

  if (slotQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 border-t border-white/[0.06] pt-4 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
        加载人物状态版本…
      </div>
    );
  }

  if (!payload || payload.versions.length === 0) return null;

  const versions = [...payload.versions].sort((left, right) => {
    if (left.version_id === payload.slot.current_version_id) return -1;
    if (right.version_id === payload.slot.current_version_id) return 1;
    return String(right.created_at ?? "").localeCompare(String(left.created_at ?? ""));
  });

  const adopt = async (versionId: string) => {
    try {
      await adoptVersion.mutateAsync({
        versionId,
        reason: "人物身份卡手动采用",
      });
      toast.success("已采用人物状态版本");
    } catch {
      toast.error("采用失败，请检查任务日志");
    }
  };

  return (
    <section className="border-t border-white/[0.06] pt-4" aria-label="人物状态三视图版本">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="text-xs font-medium text-foreground">人物状态三视图</h4>
          <p className="mt-1 text-[11px] text-muted-foreground">
            正面、侧面、背面保持同一人物与服装；生成后先作为候选，可手动采用。
          </p>
        </div>
        <span className="rounded-full border border-border px-2 py-1 text-[10px] text-muted-foreground">
          {versions.length} 个版本
        </span>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        {versions.map((version) => {
          const isCurrent = version.version_id === payload.slot.current_version_id;
          const canAdopt =
            !isCurrent &&
            version.adoption_status === "candidate" &&
            version.qc_passed &&
            !payload.read_only;
          const issues = [
            ...version.soft_issues,
            ...(version.technical_error ? [version.technical_error] : []),
          ];

          return (
            <article
              key={version.version_id}
              className="overflow-hidden rounded-[8px] border border-border bg-background/30"
            >
              <img
                src={assetMediaUrl(project, version.asset_path)}
                alt={`${characterName} ${identityId} 三视图 ${version.version_id}`}
                className="aspect-video w-full bg-white/[0.025] object-contain"
              />
              <div className="space-y-2 p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-1 text-xs font-medium">
                    {version.qc_passed ? (
                      <Check className="size-3 text-emerald-400" />
                    ) : (
                      <AlertTriangle className="size-3 text-amber-400" />
                    )}
                    {statusLabel(version, isCurrent)}
                  </span>
                  <span className="max-w-[55%] truncate font-mono text-[10px] text-muted-foreground">
                    {version.version_id}
                  </span>
                </div>

                <div className="flex flex-wrap gap-1">
                  {panelLayout(version).map((panel) => (
                    <span
                      key={panel}
                      className="rounded bg-white/[0.05] px-1.5 py-0.5 text-[10px] text-muted-foreground"
                    >
                      {PANEL_LABELS[panel] ?? panel}
                    </span>
                  ))}
                </div>

                {issues.length > 0 && (
                  <div className="rounded bg-amber-500/10 px-2 py-1.5 text-[10px] text-amber-200">
                    {issues.join("；")}
                  </div>
                )}

                {!isCurrent && version.adoption_status === "candidate" && (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-7 w-full text-xs"
                    disabled={!canAdopt || adoptVersion.isPending}
                    onClick={() => void adopt(version.version_id)}
                  >
                    {adoptVersion.isPending ? (
                      <Loader2 className="size-3 animate-spin" />
                    ) : null}
                    采用此版本
                  </Button>
                )}
              </div>
            </article>
          );
        })}
      </div>

      {payload.read_only && payload.read_only_reason ? (
        <p className="mt-2 text-[10px] text-amber-300">{payload.read_only_reason}</p>
      ) : null}
    </section>
  );
}
