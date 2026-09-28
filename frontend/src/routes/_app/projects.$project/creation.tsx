// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createFileRoute } from "@tanstack/react-router";
import { ScriptWorkspace } from "@/features/script-creation/script-workspace";

function ProjectScriptCreationPage() {
  const { project } = Route.useParams();
  const { document } = Route.useSearch();
  return <ScriptWorkspace project={project} initialDocumentId={document} />;
}

export const Route = createFileRoute("/_app/projects/$project/creation")({
  validateSearch: (search: Record<string, unknown>) => ({ document: typeof search.document === "string" ? search.document : undefined }),
  component: ProjectScriptCreationPage,
});
