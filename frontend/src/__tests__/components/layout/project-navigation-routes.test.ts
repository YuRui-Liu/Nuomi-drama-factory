// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  PROJECT_NAV_ITEMS,
  PROJECT_SECTION_ROUTES,
  projectEntryRoute,
  projectModeFromPath,
  projectSectionFromPath,
} from "@/components/layout/project-navigation-routes";

describe("project navigation routes", () => {
  it("exposes the approved direct navigation order without changing routes", () => {
    expect(PROJECT_NAV_ITEMS.map((item) => item.labelKey)).toEqual([
      "nav.freezone",
      "nav.assets",
      "nav.styles",
    ]);
    expect(PROJECT_NAV_ITEMS.map((item) => item.to)).toEqual([
      "/projects/$project/freezone",
      "/projects/$project/characters",
      "/projects/$project/styles",
    ]);
    expect(projectSectionFromPath("/projects/demo/ingest")).toBe("ingest");
    expect(projectSectionFromPath("/projects/demo/creation")).toBe("creation");
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

  it("opens the canvas when a project card remembers the legacy creation page", () => {
    expect(projectEntryRoute("creation")).toBe(PROJECT_SECTION_ROUTES.freezone);
    expect(projectEntryRoute(undefined)).toBe(PROJECT_SECTION_ROUTES.freezone);
    expect(projectEntryRoute("characters")).toBe(PROJECT_SECTION_ROUTES.characters);
    // The compatibility route remains explicitly addressable.
    expect(projectSectionFromPath("/projects/demo/creation")).toBe("creation");
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
