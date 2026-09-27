// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createFileRoute } from "@tanstack/react-router";
import { ScriptWorkspace } from "@/features/script-creation/script-workspace";

function ProjectScriptCreationPage() {
  const { project } = Route.useParams();
  return <ScriptWorkspace project={project} />;
}

export const Route = createFileRoute("/_app/projects/$project/creation")({
  component: ProjectScriptCreationPage,
});
