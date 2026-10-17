import { beforeEach, expect, it, vi } from "vitest";
import { encodeBriefSettings, defaultSettings } from "@/features/script-creation/settings";
import type { ScriptRevision } from "@/features/script-creation/types";

const api = vi.hoisted(() => ({ revisions: vi.fn() }));
vi.mock("@/features/script-creation/api", () => ({ scriptCreationApi: api }));

import { readPlanningSettings } from "@/features/canvas/production/ideation/planningSettings";

function revision(id: string, settings: ReturnType<typeof defaultSettings>): ScriptRevision {
  return {
    id, document_id: "brief-1", parent_revision_id: null,
    markdown: encodeBriefSettings(settings), blocks: [], client_mutation_id: "",
    created_at: "", restored_from_revision_id: null,
  };
}

beforeEach(() => vi.clearAllMocks());

it("reads planning settings from the explicitly adopted revision, not the latest revision", async () => {
  const adopted = { ...defaultSettings(), mode: "single" as const, episodeCount: 1, idea: "采用的构想" };
  const latest = { ...defaultSettings(), mode: "series" as const, episodeCount: 24, idea: "最新构想" };
  api.revisions.mockResolvedValue([revision("revision-b", latest), revision("revision-a", adopted)]);

  const result = await readPlanningSettings("project-1", "brief-1", "revision-a");

  expect(api.revisions).toHaveBeenCalledWith("project-1", "brief-1");
  expect(result).toEqual(adopted);
});

it("throws a clear Chinese error when the requested revision is missing", async () => {
  api.revisions.mockResolvedValue([revision("revision-b", defaultSettings())]);

  await expect(readPlanningSettings("project-1", "brief-1", "missing-revision"))
    .rejects.toThrow("未找到指定的创作简报版本（missing-revision）");
});

it("throws a clear Chinese error when the requested revision has no decodable settings", async () => {
  api.revisions.mockResolvedValue([{ ...revision("revision-a", defaultSettings()), markdown: "# 普通简报" }]);

  await expect(readPlanningSettings("project-1", "brief-1", "revision-a"))
    .rejects.toThrow("指定的创作简报版本缺少有效的创作设定（revision-a）");
});
