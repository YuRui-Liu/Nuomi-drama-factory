// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import ky from "ky";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/" }),
}));

import { useGenerateRewrite } from "@/lib/queries/scripts";

const server = setupServer();

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("story adaptation query", () => {
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
