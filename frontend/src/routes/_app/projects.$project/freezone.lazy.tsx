// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createLazyFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

import type { SupertaleProjectSummary } from "@/api/projects";
import { GlobalErrorDialog } from "@/components/GlobalErrorDialog";
import {
  subscribeOpenGlobalErrorDialog,
  type GlobalErrorDialogDetail,
} from "@/features/app/errorDialogEvents";
import { CreationWorkspace } from "@/features/creation-workspace/CreationWorkspace";
import { useAllProjectSummaries } from "@/lib/queries/projects";

function FreezoneProjectRoute() {
  const { project } = Route.useParams();
  const { data: projects, isLoading } = useAllProjectSummaries();
  const [globalError, setGlobalError] = useState<GlobalErrorDialogDetail | null>(null);


  useEffect(() => subscribeOpenGlobalErrorDialog(setGlobalError), []);

  const freezoneProjects = useMemo<SupertaleProjectSummary[]>(
    () =>
      (projects ?? []).map((item) => ({
        id: item.id,
        name: item.name,
        display_name: item.name,
        updated_at: item.updatedAt,
        episode_count: item.episodeCount,
        creation_mode: item.creationMode,
      })),
    [projects],
  );
  const matchedProject = useMemo(
    () =>
      freezoneProjects.find((item) => item.id === project) ??
      freezoneProjects.find((item) => item.name === project) ??
      null,
    [freezoneProjects, project],
  );

  if (isLoading || !projects) {
    return (
      <div className="-m-6 flex h-[calc(100%+3rem)] items-center justify-center bg-bg-dark text-text-muted">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-accent border-t-transparent" />
      </div>
    );
  }

  if (!matchedProject) {
    return (
      <div className="-m-6 flex h-[calc(100%+3rem)] items-center justify-center bg-bg-dark">
        <div className="max-w-md rounded-2xl border border-border-default bg-surface px-6 py-8 text-center">
          <div className="mb-2 text-base font-medium text-text">项目不存在</div>
          <div className="mb-6 text-sm text-text-muted">
            当前账号下找不到项目 <code className="rounded bg-bg-dark px-1 py-0.5">{project}</code>。
          </div>
          <a
            href="/"
            className="rounded-lg bg-accent/90 px-4 py-2 text-sm text-white transition hover:bg-accent"
          >
            返回项目
          </a>
        </div>
      </div>
    );
  }

  return (
      <div className="-m-6 h-[calc(100%+3rem)] w-[calc(100%+3rem)] bg-bg-dark">
        <CreationWorkspace key={matchedProject.id} project={matchedProject} />
        <GlobalErrorDialog
          isOpen={Boolean(globalError)}
          title={globalError?.title ?? ""}
          message={globalError?.message ?? ""}
          details={globalError?.details}
          copyText={globalError?.copyText}
          onClose={() => setGlobalError(null)}
        />
      </div>
  );
}

export const Route = createLazyFileRoute("/_app/projects/$project/freezone")({
  component: FreezoneProjectRoute,
});
