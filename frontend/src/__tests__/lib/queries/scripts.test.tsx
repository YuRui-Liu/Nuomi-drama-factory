// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/__mocks__/msw/server";
import ky from "ky";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/" }),
}));

import { useGenerateRewrite, useUpdateBeat } from "@/lib/queries/scripts";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("story adaptation query", () => {
  it.each([false, true])("serializes beat saves across editor instances (first save fails: %s)", async (firstFails) => {
    let finishFirst!: () => void;
    const firstResponse = new Promise<void>((resolve) => { finishFirst = resolve; });
    const received: string[] = [];
    server.use(http.patch("http://localhost:3000/api/v1/projects/demo/episodes/1/beats/1", async ({ request }) => {
      const body = await request.clone().json() as { narration_segment: string };
      received.push(body.narration_segment);
      if (body.narration_segment === "first") await firstResponse;
      if (firstFails && body.narration_segment === "first") return HttpResponse.json({ error: "save failed" }, { status: 500 });
      return HttpResponse.json({ ok: true, data: { beat_number: 1, ...body } });
    }));
    const hook = renderHook(() => ({ first: useUpdateBeat("demo", 1), second: useUpdateBeat("demo", 1) }), { wrapper });
    let first!: Promise<unknown>;
    let second!: Promise<unknown>;
    try {
      act(() => { first = hook.result.current.first.mutateAsync({ beatNum: 1, data: { narration_segment: "first" } }); });
      await waitFor(() => expect(received).toEqual(["first"]));
      act(() => { second = hook.result.current.second.mutateAsync({ beatNum: 1, data: { narration_segment: "newest" } }); });
      await waitFor(() => expect(hook.result.current.second.isPaused || received.length === 2).toBe(true));
      expect(received).toEqual(["first"]);
    } finally {
      finishFirst();
      await act(async () => { await Promise.allSettled([first, second]); });
      hook.unmount();
    }
    expect(received).toEqual(["first", "newest"]);
  });

  it("uses the canonical rewrite endpoint and preserves structured errors", async () => {
    let requestedPath = "";
    let receivedBody: unknown = undefined;
    server.use(
      http.post(
        "http://localhost:3000/api/v1/projects/demo/episodes/1/rewrite/generate",
        async ({ request }) => {
          requestedPath = new URL(request.url).pathname;
          receivedBody = await request.clone().json();
          return HttpResponse.json({
            ok: false,
            code: "identity_plan_required",
            error: "请先规划本集身份",
          });
        },
      ),
    );

    const { result } = renderHook(() => useGenerateRewrite("demo", 1), {
      wrapper,
    });

    result.current.mutate({});

    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(requestedPath).toBe("/api/v1/projects/demo/episodes/1/rewrite/generate");
    expect(receivedBody).toEqual({});
    expect(result.current.data).toEqual({
      ok: false,
      code: "identity_plan_required",
      error: "请先规划本集身份",
    });
  });

  it("surfaces transport failures from story adaptation", async () => {
    server.use(
      http.post(
        "http://localhost:3000/api/v1/projects/demo/episodes/1/rewrite/generate",
        () => HttpResponse.error(),
      ),
    );

    const { result } = renderHook(() => useGenerateRewrite("demo", 1), {
      wrapper,
    });

    await expect(result.current.mutateAsync({})).rejects.toThrow();
  });
});
