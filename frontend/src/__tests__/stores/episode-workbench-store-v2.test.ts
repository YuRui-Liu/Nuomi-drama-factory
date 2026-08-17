import { beforeEach, describe, expect, it } from "vitest";

import { DEFAULT_VIDEO_MODEL, useEpisodeWorkbenchStore } from "@/stores/episode-workbench-store";

describe("episode workbench v2 state", () => {
  beforeEach(() => useEpisodeWorkbenchStore.getState().reset());

  it("persists the selected narrative group and project video default", () => {
    const state = useEpisodeWorkbenchStore.getState();
    state.setNarrativeGroupSelection({ project: "demo", episode: 1 }, "ng-02");
    state.setProjectVideoModel("demo", "runninghub:minimax-h3");
    expect(useEpisodeWorkbenchStore.getState().narrativeGroupSelectionByScope["demo:1"]).toBe("ng-02");
    expect(useEpisodeWorkbenchStore.getState().projectVideoModelByProject.demo).toBe(DEFAULT_VIDEO_MODEL);
  });

  it("defaults to H3 when no project preference exists", () => {
    expect(useEpisodeWorkbenchStore.getState().getProjectVideoModel("new-project"))
      .toBe("runninghub:minimax-h3");
  });
});
