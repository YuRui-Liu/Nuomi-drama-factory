import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import ky from "ky";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { createElement, type ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/" }),
}));

import { queryKeys } from "@/lib/query-keys";
import {
  directorPlanKeys,
  useAbandonDirectorPlan,
  useActivateDirectorPlan,
  useCreateDirectorPlan,
  useDirectorPlan,
  useDirectorPlanComparison,
  useDirectorPlanMigration,
  useDirectorPlans,
  useEditDirectorPlan,
  useUpdateDirectorPlanMigration,
  type DirectorPlanRevision,
  type MigrationDecision,
} from "@/lib/queries/director-plans";

const server = setupServer();

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const revision: DirectorPlanRevision = {
  revision_id: "rev-2",
  parent_revision_id: "rev-1",
  episode: 1,
  status: "review_required",
  source_script_hash: "script-hash",
  director_model: "deepseek-v4-flash",
  prompt_version: "director-v2",
  project_style_snapshot_id: "style-1",
  groups: [{
    id: "ng-02",
    ordinal: 1,
    source_span_ids: ["span-1"],
    scene_anchor: "workshop",
    time_anchor: "day",
    objective: "repair the radio",
    visible_turn: "the radio lights up",
    relation_to_previous: "single",
    style_snapshot_id: "style-1",
    shots: [{
      id: "shot-5",
      source_span_ids: ["span-1"],
      subject: "A Yuan",
      action: "turns the dial",
      visible_start_state: "radio dark",
      visible_end_state: "radio lit",
      shot_size: "medium",
      camera_angle: "eye_level",
      composition: "centered",
      camera_motion: "static",
      dialogue_source_ids: [],
      duration_seconds: 4,
    }],
  }],
  validation_report: { passed: true, issues: [], version: 1 },
  migration_report: { items: [] },
  created_at: "2026-08-29T00:00:00Z",
  activated_at: null,
};

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

describe("director plan query contract", () => {
  it("represents low-confidence assets as unmatched", () => {
    const decision: MigrationDecision = "unmatched";

    expect(decision).toBe("unmatched");
  });

  it("loads list, detail, comparison and migration from stable cache keys", async () => {
    server.use(
      http.get("http://localhost:3000/api/v1/projects/demo/episodes/1/director-plans", () =>
        HttpResponse.json({ ok: true, data: [revision] })),
      http.get("http://localhost:3000/api/v1/projects/demo/episodes/1/director-plans/rev-2", () =>
        HttpResponse.json({ ok: true, data: revision })),
      http.get("http://localhost:3000/api/v1/projects/demo/episodes/1/director-plans/rev-2/comparison", ({ request }) => {
        expect(new URL(request.url).searchParams.get("base")).toBe("rev-1");
        return HttpResponse.json({ ok: true, data: { base: revision, candidate: revision } });
      }),
      http.get("http://localhost:3000/api/v1/projects/demo/episodes/1/director-plans/rev-2/migration", () =>
        HttpResponse.json({ ok: true, data: { items: [] } })),
    );

    const client = makeClient();
    const wrapper = createWrapper(client);
    const list = renderHook(() => useDirectorPlans("demo", 1), { wrapper });
    const detail = renderHook(() => useDirectorPlan("demo", 1, "rev-2"), { wrapper });
    const comparison = renderHook(
      () => useDirectorPlanComparison("demo", 1, "rev-2", "rev-1"),
      { wrapper },
    );
    const migration = renderHook(() => useDirectorPlanMigration("demo", 1, "rev-2"), { wrapper });

    await waitFor(() => expect(list.result.current.data?.ok && list.result.current.data.data).toHaveLength(1));
    await waitFor(() => expect(detail.result.current.data?.ok && detail.result.current.data.data.revision_id).toBe("rev-2"));
    await waitFor(() => expect(comparison.result.current.data?.ok && comparison.result.current.data.data.base).toBeDefined());
    await waitFor(() => expect(migration.result.current.data?.ok && migration.result.current.data.data.items).toEqual([]));

    expect(directorPlanKeys.detail("demo", 1, "rev-2")).toEqual([
      "projects", "demo", "episodes", 1, "director-plans", "rev-2",
    ]);
  });

  it("posts a split edit and invalidates director plan and narrative group keys", async () => {
    let body: unknown;
    server.use(http.post(
      "http://localhost:3000/api/v1/projects/demo/episodes/1/director-plans/rev-2/edits",
      async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ok: true, data: { ...revision, revision_id: "rev-3" } });
      },
    ));
    const client = makeClient();
    client.setQueryData(directorPlanKeys.detail("demo", 1, "rev-2"), revision);
    client.setQueryData(queryKeys.narrativeGroups("demo", 1), []);
    const { result } = renderHook(() => useEditDirectorPlan("demo", 1), {
      wrapper: createWrapper(client),
    });

    await result.current.mutateAsync({
      revisionId: "rev-2",
      command: { kind: "split_group", group_id: "ng-02", before_shot_id: "shot-5" },
    });

    expect(body).toEqual({ kind: "split_group", group_id: "ng-02", before_shot_id: "shot-5" });
    expect(client.getQueryState(directorPlanKeys.detail("demo", 1, "rev-2"))?.isInvalidated).toBe(true);
    expect(client.getQueryState(queryKeys.narrativeGroups("demo", 1))?.isInvalidated).toBe(true);
  });

  it("creates, activates, abandons and reviews migration decisions through canonical endpoints", async () => {
    const requests: Array<{ method: string; path: string; body: unknown }> = [];
    server.use(
      http.post("http://localhost:3000/api/v1/projects/demo/episodes/1/director-plans", async ({ request }) => {
        requests.push({ method: request.method, path: new URL(request.url).pathname, body: await request.json() });
        return HttpResponse.json({ ok: true, task_type: "director_plan", task_id: "task-1" });
      }),
      http.post("http://localhost:3000/api/v1/projects/demo/episodes/1/director-plans/rev-2/activate", async ({ request }) => {
        requests.push({ method: request.method, path: new URL(request.url).pathname, body: await request.json() });
        return HttpResponse.json({ ok: true, data: { ...revision, status: "active" } });
      }),
      http.post("http://localhost:3000/api/v1/projects/demo/episodes/1/director-plans/rev-2/abandon", async ({ request }) => {
        requests.push({ method: request.method, path: new URL(request.url).pathname, body: await request.json() });
        return HttpResponse.json({ ok: true, data: { ...revision, status: "abandoned" } });
      }),
      http.put("http://localhost:3000/api/v1/projects/demo/episodes/1/director-plans/rev-2/migration/item-1", async ({ request }) => {
        requests.push({ method: request.method, path: new URL(request.url).pathname, body: await request.json() });
        return HttpResponse.json({ ok: true, data: { item_id: "item-1", decision: "reference_only" } });
      }),
    );

    const client = makeClient();
    client.setQueryData(directorPlanKeys.list("demo", 1), []);
    client.setQueryData(queryKeys.narrativeGroups("demo", 1), []);
    const wrapper = createWrapper(client);
    const create = renderHook(() => useCreateDirectorPlan("demo", 1), { wrapper });
    const activate = renderHook(() => useActivateDirectorPlan("demo", 1), { wrapper });
    const abandon = renderHook(() => useAbandonDirectorPlan("demo", 1), { wrapper });
    const migration = renderHook(() => useUpdateDirectorPlanMigration("demo", 1), { wrapper });

    await create.result.current.mutateAsync();
    await activate.result.current.mutateAsync({ revisionId: "rev-2" });
    await abandon.result.current.mutateAsync({ revisionId: "rev-2" });
    await migration.result.current.mutateAsync({
      revisionId: "rev-2",
      itemId: "item-1",
      decision: "reference_only",
    });

    expect(requests.map(({ method, path, body: requestBody }) => ({ method, path, body: requestBody }))).toEqual([
      { method: "POST", path: "/api/v1/projects/demo/episodes/1/director-plans", body: {} },
      { method: "POST", path: "/api/v1/projects/demo/episodes/1/director-plans/rev-2/activate", body: {} },
      { method: "POST", path: "/api/v1/projects/demo/episodes/1/director-plans/rev-2/abandon", body: {} },
      { method: "PUT", path: "/api/v1/projects/demo/episodes/1/director-plans/rev-2/migration/item-1", body: { decision: "reference_only" } },
    ]);
    expect(client.getQueryState(directorPlanKeys.list("demo", 1))?.isInvalidated).toBe(true);
    expect(client.getQueryState(queryKeys.narrativeGroups("demo", 1))?.isInvalidated).toBe(true);
  });
});
