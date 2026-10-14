// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

export const PROJECT_SECTION_ROUTES = {
  freezone: "/projects/$project/freezone",
  ingest: "/projects/$project/ingest",
  creation: "/projects/$project/creation",
  characters: "/projects/$project/characters",
  episodes: "/projects/$project/episodes",
  styles: "/projects/$project/styles",
  tasks: "/projects/$project/tasks",
  costs: "/projects/$project/costs",
  assistant: "/projects/$project/assistant",
  studios: "/projects/$project/studios",
} as const;

export type ProjectSection = keyof typeof PROJECT_SECTION_ROUTES;

export const PROJECT_NAV_ITEMS = [
  { labelKey: "nav.freezone", to: PROJECT_SECTION_ROUTES.freezone },
  { labelKey: "nav.assets", to: PROJECT_SECTION_ROUTES.characters },
  { labelKey: "nav.styles", to: PROJECT_SECTION_ROUTES.styles },
];

export const PROJECT_MANAGEMENT_ITEMS = [
  { labelKey: "nav.tasks", to: PROJECT_SECTION_ROUTES.tasks },
  { labelKey: "nav.costs", to: PROJECT_SECTION_ROUTES.costs },
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
