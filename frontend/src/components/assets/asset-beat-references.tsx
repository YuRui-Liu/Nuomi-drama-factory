// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Link } from "@tanstack/react-router";
import { Film } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import type { BeatReference } from "@/lib/queries/asset-references";
import { cn } from "@/lib/utils";

/**
 * Reverse usage list for an asset edit surface. Each entry deep-links to its
 * narrative group, legacy beat, or episode asset binding.
 * References come from the caller's detail-scoped aggregate reference query.
 */
export function AssetBeatReferences({
  project,
  references,
  className,
}: {
  project: string;
  references: BeatReference[];
  className?: string;
}) {
  const { t } = useTranslation();

  const sorted = useMemo(
    () =>
      [...references].sort(
        (a, b) => a.episode - b.episode || (a.groupOrdinal ?? a.beatNumber ?? 0) - (b.groupOrdinal ?? b.beatNumber ?? 0),
      ),
    [references],
  );

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <Film className="size-3.5" />
        剧情引用
        <span className="tabular-nums">({sorted.length})</span>
      </div>
      {sorted.length === 0 ? (
        <p className="text-xs text-muted-foreground/70">
          暂无剧集、叙事组或镜头引用
        </p>
      ) : (
        <div className="flex max-h-[200px] flex-wrap gap-1.5 overflow-y-auto overscroll-contain pr-1">
          {sorted.map((ref) => (
            <Link
              key={`${ref.episode}:${ref.groupId ?? ref.beatNumber ?? "binding"}`}
              to={ref.binding ? "/projects/$project/episodes/$episode/script" : "/projects/$project/episodes/$episode/beats"}
              params={{ project, episode: String(ref.episode) }}
              search={(ref.groupId ? { group: ref.groupId, sub: "render" } : ref.beatNumber != null ? { beat: ref.beatNumber } : {}) as never}
              hash={ref.binding ? "script-assets" : ref.groupId ? undefined : `beat-${ref.beatNumber}`}
              className="inline-flex items-center rounded-[6px] border border-border bg-background/40 px-2 py-0.5 text-[11px] text-muted-foreground transition hover:border-primary/40 hover:text-foreground"
            >
              {ref.binding ? `第 ${ref.episode} 集 · 资产绑定` : ref.groupId ? `第 ${ref.episode} 集 · 叙事组 ${ref.groupOrdinal ?? ref.groupId}` : t("assets.common.beatRef", {
                episode: ref.episode,
                beat: ref.beatNumber,
              })}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
