// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

export const PROJECT_SECTION_ROUTES = {
  freezone: "/projects/$project/freezone",
  ingest: "/projects/$project/ingest",
  characters: "/projects/$project/characters",
  episodes: "/projects/$project/episodes",
  styles: "/projects/$project/styles",
  tasks: "/projects/$project/tasks",
  assistant: "/projects/$project/assistant",
} as const;

export type ProjectSection = keyof typeof PROJECT_SECTION_ROUTES;

export const PROJECT_NAV_ITEMS = [
  { labelKey: "nav.ingest", to: PROJECT_SECTION_ROUTES.ingest },
  { labelKey: "nav.assets", to: PROJECT_SECTION_ROUTES.characters },
  { labelKey: "nav.episodes", to: PROJECT_SECTION_ROUTES.episodes },
  { labelKey: "nav.freezone", to: PROJECT_SECTION_ROUTES.freezone },
  { labelKey: "nav.styles", to: PROJECT_SECTION_ROUTES.styles },
  { labelKey: "nav.tasks", to: PROJECT_SECTION_ROUTES.tasks },
  { labelKey: "nav.aiAssistant", to: PROJECT_SECTION_ROUTES.assistant },
] as const;

export function projectSectionFromPath(pathname: string): ProjectSection | null {
  const segment = pathname.match(/^\/projects\/[^/]+\/([^/]+)/)?.[1];
  return segment && segment in PROJECT_SECTION_ROUTES
    ? (segment as ProjectSection)
    : null;
}

export function projectModeFromPath(pathname: string): "xiahua" | "xiaji" {
  return projectSectionFromPath(pathname) === "freezone" ? "xiahua" : "xiaji";
}
