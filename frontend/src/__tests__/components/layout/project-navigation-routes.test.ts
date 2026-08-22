// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  PROJECT_NAV_ITEMS,
  PROJECT_SECTION_ROUTES,
  projectModeFromPath,
  projectSectionFromPath,
} from "@/components/layout/project-navigation-routes";

describe("project navigation routes", () => {
  it("exposes the approved direct navigation order without changing routes", () => {
    expect(PROJECT_NAV_ITEMS.map((item) => item.labelKey)).toEqual([
      "nav.ingest",
      "nav.assets",
      "nav.episodes",
      "nav.freezone",
      "nav.styles",
      "nav.tasks",
      "nav.aiAssistant",
    ]);
    expect(PROJECT_NAV_ITEMS.map((item) => item.to)).toEqual([
      "/projects/$project/ingest",
      "/projects/$project/characters",
      "/projects/$project/episodes",
      "/projects/$project/freezone",
      "/projects/$project/styles",
      "/projects/$project/tasks",
      "/projects/$project/assistant",
    ]);
    expect(projectSectionFromPath("/projects/demo/ingest")).toBe("ingest");
    expect(projectSectionFromPath("/projects/demo/characters")).toBe("characters");
    expect(projectSectionFromPath("/projects/demo/episodes/12")).toBe("episodes");
    expect(projectSectionFromPath("/projects/demo/freezone")).toBe("freezone");
    expect(projectSectionFromPath("/projects/demo/styles")).toBe("styles");
    expect(projectSectionFromPath("/projects/demo/tasks")).toBe("tasks");
    expect(projectSectionFromPath("/projects/demo/assistant")).toBe("assistant");
  });

  it("uses freezone as the project dashboard entry", () => {
    expect(PROJECT_SECTION_ROUTES.freezone).toBe("/projects/$project/freezone");
  });

  it("classifies freezone as xiahua and every production section as xiaji", () => {
    expect(projectModeFromPath("/projects/demo/freezone")).toBe("xiahua");
    expect(projectModeFromPath("/projects/demo/ingest")).toBe("xiaji");
    expect(projectModeFromPath("/projects/demo/tasks")).toBe("xiaji");
  });

  it("preserves the tasks section when switching projects", () => {
    expect(projectSectionFromPath("/projects/demo/tasks")).toBe("tasks");
  });

  it("does not silently classify unknown project sections as freezone", () => {
    expect(projectSectionFromPath("/projects/demo/unknown")).toBeNull();
  });
});
