import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import ky from "ky";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/" }),
}));

import { queryKeys } from "@/lib/query-keys";
import { useRecordObservedBoundary } from "@/lib/queries/shot-continuity";

const server = setupServer();

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapperWithClient(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe("shot continuity query", () => {
  it("puts observed evidence and invalidates group and prompt caches", async () => {
    let requestedPath = "";
    let requestedBody: unknown;
    server.use(http.put(
      "http://localhost:3000/api/v1/projects/demo/episodes/3/narrative-groups/group-1/video/segments/segment-8/continuity",
      async ({ request }) => {
        requestedPath = new URL(request.url).pathname;
        requestedBody = await request.json();
        return HttpResponse.json({
          ok: true,
          data: { units: [], stale_dependent_shot_ids: ["shot-next"] },
        });
      },
    ));
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const groupKey = queryKeys.narrativeGroups("demo", 3);
    const promptKey = [...groupKey, "group-1", "video", "prompts"];
    client.setQueryData(groupKey, []);
    client.setQueryData(promptKey, { ok: true, data: { units: [] } });
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useRecordObservedBoundary("demo", 3, "group-1"), {
      wrapper: wrapperWithClient(client),
    });

    const response = await result.current.mutateAsync({
      segmentId: "segment-8",
      contractRevision: 2,
      observedCarryOut: "left hand holds lantern",
      acceptDeviation: true,
      deviationReason: "accepted hand change",
      lockViolations: ["identity", "prop"],
    });

    expect(requestedPath).toBe("/api/v1/projects/demo/episodes/3/narrative-groups/group-1/video/segments/segment-8/continuity");
    expect(requestedBody).toEqual({
      contract_revision: 2,
      observed_carry_out: "left hand holds lantern",
      accept_deviation: true,
      deviation_reason: "accepted hand change",
      lock_violations: ["identity", "prop"],
    });
    expect(response.ok).toBe(true);
    if (!response.ok) throw new Error(response.error);
    expect(response.data.stale_dependent_shot_ids).toEqual(["shot-next"]);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: groupKey });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: promptKey });
  });
});
