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

export type SceneReferenceKind = "master" | "reverse_master" | "spatial_layout";

interface SceneReferenceVersionsProps {
  project: string;
  sceneName: string;
  kind: SceneReferenceKind;
  baseSceneId?: string | null;
  legacyAssetPath?: string | null;
}

const KIND_LABELS: Record<SceneReferenceKind, string> = {
  master: "主参考图",
  reverse_master: "反向参考图",
  spatial_layout: "空间布局图",
};

function assetMediaUrl(project: string, assetPath: string): string {
  const encodedPath = assetPath.replace(/\\/g, "/").split("/").map((part) => encodeURIComponent(part)).join("/");
  return resolveMediaUrl(`/api/v1/projects/${encodeURIComponent(project)}/media/${encodedPath}`) ?? "";
}

function statusLabel(version: ProductionAssetVersion, isCurrent: boolean): string {
  if (isCurrent) return "当前采用";
  if (!version.qc_passed) return "QC 未通过";
  if (version.adoption_status === "candidate") return "候选版本";
  if (version.adoption_status === "superseded") return "历史版本";
  return version.adoption_status;
}

export function SceneReferenceVersions({
  project,
  sceneName,
  kind,
  baseSceneId,
  legacyAssetPath,
}: SceneReferenceVersionsProps) {
  const normalizedBaseId = baseSceneId?.trim();
  const slotId = normalizedBaseId
    ? `scene:${normalizedBaseId}:state:${sceneName}:${kind}`
    : `scene:${sceneName}:base:${kind}`;
  const assetKind = normalizedBaseId ? "scene_state" : "scene_base";
  const slotQuery = useProductionAssetSlot(project, slotId, assetKind, legacyAssetPath ?? undefined);
  const adoptVersion = useAdoptProductionAssetVersion(project, slotId);
  const payload = slotQuery.data?.ok ? slotQuery.data.data : undefined;

  if (slotQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 rounded-[10px] border border-border bg-white/[0.02] p-3 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
        加载{KIND_LABELS[kind]}版本…
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
      await adoptVersion.mutateAsync({ versionId, reason: `场景${KIND_LABELS[kind]}手动采用` });
      toast.success(`已采用${KIND_LABELS[kind]}版本`);
    } catch {
      toast.error("采用失败，请检查任务日志");
    }
  };

  return (
    <section className="rounded-[10px] border border-border bg-white/[0.02] p-3" aria-label={`${KIND_LABELS[kind]}版本`}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="text-xs font-medium text-foreground">{KIND_LABELS[kind]}版本</h4>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {normalizedBaseId ? `状态场景，继承基础场景 ${normalizedBaseId}` : "基础场景锚点"}；生成结果先进入候选区。
          </p>
        </div>
        <span className="rounded-full border border-border px-2 py-1 text-[10px] text-muted-foreground">{versions.length} 个版本</span>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        {versions.map((version) => {
          const isCurrent = version.version_id === payload.slot.current_version_id;
          const canAdopt = !isCurrent && version.adoption_status === "candidate" && version.qc_passed && !payload.read_only;
          const issues = [...version.soft_issues, ...(version.technical_error ? [version.technical_error] : [])];
          const anchorView = version.generation_metadata?.anchor_view;
          return (
            <article key={version.version_id} className="overflow-hidden rounded-[8px] border border-border bg-background/30">
              <img src={assetMediaUrl(project, version.asset_path)} alt={`${sceneName} ${KIND_LABELS[kind]} ${version.version_id}`} className="aspect-video w-full bg-white/[0.025] object-contain" />
              <div className="space-y-2 p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-1 text-xs font-medium">
                    {version.qc_passed ? <Check className="size-3 text-emerald-400" /> : <AlertTriangle className="size-3 text-amber-400" />}
                    {statusLabel(version, isCurrent)}
                  </span>
                  <span className="max-w-[55%] truncate font-mono text-[10px] text-muted-foreground">{version.version_id}</span>
                </div>
                <div className="flex flex-wrap gap-1 text-[10px] text-muted-foreground">
                  <span className="rounded bg-white/[0.05] px-1.5 py-0.5">{normalizedBaseId ? "状态资产" : "基础资产"}</span>
                  {typeof anchorView === "string" && anchorView ? <span className="rounded bg-white/[0.05] px-1.5 py-0.5">锚点：{anchorView}</span> : null}
                </div>
                {issues.length > 0 ? <div className="rounded bg-amber-500/10 px-2 py-1.5 text-[10px] text-amber-200">{issues.join("；")}</div> : null}
                {!isCurrent && version.adoption_status === "candidate" ? (
                  <Button type="button" size="sm" variant="outline" className="h-7 w-full text-xs" disabled={!canAdopt || adoptVersion.isPending} onClick={() => void adopt(version.version_id)}>
                    {adoptVersion.isPending ? <Loader2 className="size-3 animate-spin" /> : null}
                    采用此版本
                  </Button>
                ) : null}
              </div>
            </article>
          );
        })}
      </div>
      {payload.read_only && payload.read_only_reason ? <p className="mt-2 text-[10px] text-amber-300">{payload.read_only_reason}</p> : null}
    </section>
  );
}
