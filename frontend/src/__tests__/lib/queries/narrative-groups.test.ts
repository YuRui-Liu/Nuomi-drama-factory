import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import ky from "ky";
import { createElement, type ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/" }),
}));

import {
  narrativeGroupActionPath,
  narrativeGroupActionPayload,
  narrativeGroupVideoPath,
  narrativeGroupVideoDialogueSourcePath,
  narrativeGroupVideoDialogueSourcePayload,
  narrativeGroupVideoPayload,
  narrativeGroupVideoTaskScope,
  narrativeGroupReferencePath,
  narrativeGroupRevisionPath,
  narrativeGroupRollbackPath,
  useNarrativeGroupAction,
  useNarrativeGroupReferences,
} from "@/lib/queries/narrative-groups";
import { queryKeys } from "@/lib/query-keys";

describe("narrative group query contract", () => {
  it("builds a scoped stage action without client credentials", () => {
    expect(narrativeGroupActionPath("demo project", 2, "ng-01", "render", "regenerate"))
      .toBe("api/v1/projects/demo%20project/episodes/2/narrative-groups/ng-01/render/regenerate");
    expect(narrativeGroupActionPayload({ revision: 3, apiKey: "must-not-leak" } as never))
      .toEqual({ revision: 3 });
  });

  it("builds one director-video request with only model, mode, and revision", () => {
    expect(narrativeGroupVideoPath("demo project", 2, "ng-01"))
      .toBe("api/v1/projects/demo%20project/episodes/2/narrative-groups/ng-01/video/generate");
    expect(narrativeGroupVideoPayload({ model: "runninghub:minimax-h3", mode: "fl2va", revision: 4 }))
      .toEqual({ model: "runninghub:minimax-h3", mode: "fl2va", revision: 4 });
  });

  it("uses the exact server H3 scope for the current video revision", () => {
    expect(narrativeGroupVideoTaskScope("ng-01", 4)).toBe("group_ng-01_video_r4");
  });

  it("uses a separate recomposition-only dialogue source endpoint", () => {
    expect(narrativeGroupVideoDialogueSourcePath("demo", 2, "ng-01"))
      .toBe("api/v1/projects/demo/episodes/2/narrative-groups/ng-01/video/dialogue-source");
  });

  it("sends the current stage revision when switching a dialogue source", () => {
    expect(narrativeGroupVideoDialogueSourcePayload({
      spanIndex: 2,
      dialogueSource: "h3_native",
      revision: 4,
    })).toEqual({ span_index: 2, dialogue_source: "h3_native", revision: 4 });
  });

  it("builds an encoded reference-preview path", () => {
    expect(narrativeGroupReferencePath("demo project", 2, "组 一", "render"))
      .toBe("api/v1/projects/demo%20project/episodes/2/narrative-groups/%E7%BB%84%20%E4%B8%80/render/references");
  });

  it("serializes only generation selection fields and preserves empty arrays", () => {
    expect(narrativeGroupActionPayload({
      revision: 7,
      selection: {
        useStyle: false,
        selectedCharacterReferenceIds: [],
        selectedSceneReferenceIds: ["scene-1"],
      },
      apiKey: "must-not-leak",
    } as never)).toEqual({
      revision: 7,
      use_style: false,
      selected_character_reference_ids: [],
      selected_scene_reference_ids: ["scene-1"],
    });
  });

  it("builds revision history and rollback endpoints with canonical stages", () => {
    expect(narrativeGroupRevisionPath("demo", 2, "ng-01", "sketch"))
      .toBe("api/v1/projects/demo/episodes/2/narrative-groups/ng-01/sketch/revisions");
    expect(narrativeGroupRollbackPath("demo", 2, "ng-01", "render", 4))
      .toBe("api/v1/projects/demo/episodes/2/narrative-groups/ng-01/render/revisions/4/rollback");
  });

  it("uses a stable cache key beneath the episode", () => {
    expect(queryKeys.narrativeGroups("demo", 2)).toEqual([
      "projects", "demo", "episodes", 2, "narrative-groups",
    ]);
  });
});

const server = setupServer();

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return createElement(QueryClientProvider, { client }, children);
}

describe("narrative group reference hooks", () => {
  it("requests references only when enabled", async () => {
    let requests = 0;
    server.use(http.get(
      "http://localhost:3000/api/v1/projects/demo/episodes/2/narrative-groups/ng-1/render/references",
      () => {
        requests += 1;
        return HttpResponse.json({ ok: true, data: { warnings: [] } });
      },
    ));

    const disabled = renderHook(
      () => useNarrativeGroupReferences("demo", 2, "ng-1", "render", false),
      { wrapper },
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(disabled.result.current.fetchStatus).toBe("idle");
    expect(requests).toBe(0);
    disabled.unmount();

    const enabled = renderHook(
      () => useNarrativeGroupReferences("demo", 2, "ng-1", "render", true),
      { wrapper },
    );
    await waitFor(() => expect(enabled.result.current.data).toBeDefined());
    expect(requests).toBeGreaterThan(0);
  });

  it("drops selection for split and sends it for generate", async () => {
    const bodies: unknown[] = [];
    server.use(http.post(
      "http://localhost:3000/api/v1/projects/demo/episodes/2/narrative-groups/ng-1/render/:action",
      async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ ok: true, task_type: "grid", scope: "grid", message: "started" });
      },
    ));
    const { result } = renderHook(() => useNarrativeGroupAction("demo", 2), { wrapper });
    const selection = {
      useStyle: true,
      selectedCharacterReferenceIds: [],
      selectedSceneReferenceIds: ["scene-1"],
    };

    await result.current.mutateAsync({ groupId: "ng-1", stage: "render", action: "split", selection });
    await result.current.mutateAsync({ groupId: "ng-1", stage: "render", action: "generate", selection });

    expect(bodies).toEqual([
      {},
      {
        use_style: true,
        selected_character_reference_ids: [],
        selected_scene_reference_ids: ["scene-1"],
      },
    ]);
  });
});
