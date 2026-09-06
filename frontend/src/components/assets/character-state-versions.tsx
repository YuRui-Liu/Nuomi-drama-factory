// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { AlertTriangle, Check, Loader2 } from "lucide-react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
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

const V1_PANELS = ["front", "side", "back"];
const V2_PANELS = ["portrait", "headlessFront", "fullBack"];

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
  if (isV2(version)) return V2_PANELS;
  const panels = version.generation_metadata?.panel_layout;
  return Array.isArray(panels) && panels.length > 0
    ? panels.filter((panel): panel is string => typeof panel === "string")
    : V1_PANELS;
}

function isV2(version: ProductionAssetVersion): boolean {
  const layoutVersion = version.generation_metadata?.layout_version;
  return layoutVersion === "identity_sheet_v2" || layoutVersion === "v2";
}

function statusLabel(
  version: ProductionAssetVersion,
  isCurrent: boolean,
  t: TFunction,
): string {
  if (isCurrent) return t("characters.stateVersions.status.current");
  if (!version.qc_passed) return t("characters.stateVersions.status.qcFailed");
  if (version.adoption_status === "candidate") return t("characters.stateVersions.status.candidate");
  if (version.adoption_status === "superseded") return t("characters.stateVersions.status.superseded");
  return version.adoption_status;
}

function qcIssues(version: ProductionAssetVersion): string[] {
  const reportIssues = version.generation_metadata?.quality_report?.issues;
  return [...new Set([
    ...(Array.isArray(reportIssues) ? reportIssues : []),
    ...version.soft_issues,
    ...(version.technical_error ? [version.technical_error] : []),
  ])];
}

export function CharacterStateVersions({
  project,
  characterName,
  identityId,
  legacyAssetPath,
}: CharacterStateVersionsProps) {
  const { t } = useTranslation();
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
        {t("characters.stateVersions.loading")}
      </div>
    );
  }

  if (!payload || payload.versions.length === 0) return null;

  const versions = [...payload.versions].sort((left, right) => {
    if (left.version_id === payload.slot.current_version_id) return -1;
    if (right.version_id === payload.slot.current_version_id) return 1;
    return String(right.created_at ?? "").localeCompare(String(left.created_at ?? ""));
  });
  const currentVersion = versions.find(
    (version) => version.version_id === payload.slot.current_version_id,
  );
  const currentLayoutIsV2 = currentVersion ? isV2(currentVersion) : false;

  const adopt = async (versionId: string) => {
    try {
      await adoptVersion.mutateAsync({
        versionId,
        reason: t("characters.stateVersions.adoptReason"),
      });
      toast.success(t("characters.stateVersions.adoptSuccess"));
    } catch {
      toast.error(t("characters.stateVersions.adoptFailed"));
    }
  };

  return (
    <section className="border-t border-white/[0.06] pt-4" aria-label={t("characters.stateVersions.ariaLabel")}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="text-xs font-medium text-foreground">
            {t(currentLayoutIsV2
              ? "characters.stateVersions.v2Title"
              : "characters.stateVersions.title")}
          </h4>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {t(currentLayoutIsV2
              ? "characters.stateVersions.v2Description"
              : "characters.stateVersions.description")}
          </p>
        </div>
        <span className="rounded-full border border-border px-2 py-1 text-[10px] text-muted-foreground">
          {t("characters.stateVersions.versionCount", { count: versions.length })}
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
          const issues = qcIssues(version);

          return (
            <article
              key={version.version_id}
              className="overflow-hidden rounded-[8px] border border-border bg-background/30"
            >
              <img
                src={assetMediaUrl(project, version.asset_path)}
                alt={t("characters.stateVersions.imageAlt", {
                  characterName,
                  identityId,
                  versionId: version.version_id,
                })}
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
                    {statusLabel(version, isCurrent, t)}
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
                      {t(`characters.stateVersions.panels.${panel}`, { defaultValue: panel })}
                    </span>
                  ))}
                </div>

                {isV2(version) ? (
                  <p className="text-[10px] text-muted-foreground">
                    {t("characters.stateVersions.isolationHint")}
                  </p>
                ) : null}

                {issues.length > 0 && (
                  <div className="rounded bg-amber-500/10 px-2 py-1.5 text-[10px] text-amber-200">
                    {issues.map((issue) =>
                      t(`characters.stateVersions.qcIssues.${issue}`, { defaultValue: issue }),
                    ).join(t("characters.stateVersions.issueSeparator"))}
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
                    {t("characters.stateVersions.adopt")}
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
