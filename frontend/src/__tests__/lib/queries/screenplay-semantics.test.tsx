import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import ky from "ky";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({ api: ky.create({ baseUrl: "http://localhost:3000/" }) }));
import { useRepairScreenplaySemantics, useRetrySemanticScene } from "@/lib/queries/screenplay-semantics";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

describe("screenplay semantic repair query contract", () => {
  it("posts the frozen revision with the default scene concurrency", async () => {
    let received: unknown;
    server.use(http.post("http://localhost:3000/api/v1/projects/demo/episodes/1/screenplay-semantics/sem-1/repair", async ({ request }) => {
      received = await request.json();
      return HttpResponse.json({ ok: true, task_type: "screenplay_semantic_repair", task_id: "task-1" });
    }));
    const { result } = renderHook(() => useRepairScreenplaySemantics("demo", 1), { wrapper });
    await result.current.mutateAsync({ revisionId: "sem-1" });
    expect(received).toEqual({ concurrency: 3 });
  });
});

describe("screenplay semantic scene query contract", () => {
  it("posts a scene-scoped retry without regenerating the whole episode", async () => {
    let path = "";
    server.use(http.post("http://localhost:3000/api/v1/projects/demo/episodes/1/screenplay-semantics/sem-1/scenes/scene-2/retry", ({ request }) => {
      path = new URL(request.url).pathname;
      return HttpResponse.json({ ok: true, task_id: "task-1" });
    }));
    const { result } = renderHook(() => useRetrySemanticScene("demo", 1), { wrapper });
    await result.current.mutateAsync({ revisionId: "sem-1", sceneId: "scene-2" });
    expect(path).toBe("/api/v1/projects/demo/episodes/1/screenplay-semantics/sem-1/scenes/scene-2/retry");
  });
});
