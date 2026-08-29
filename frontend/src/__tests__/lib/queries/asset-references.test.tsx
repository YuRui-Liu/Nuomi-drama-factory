// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import ky from "ky";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/", retry: 0 }),
  uploadApi: ky.create({ baseUrl: "http://localhost:3000/", retry: 0 }),
}));

import {
  useAssetReferences,
  type AssetRef,
} from "@/lib/queries/asset-references";
import { server } from "@/__mocks__/msw/server";

const queryClients = new Set<QueryClient>();

afterEach(() => {
  for (const client of queryClients) client.clear();
  queryClients.clear();
});

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  queryClients.add(qc);
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

describe("useAssetReferences", () => {
  it("requests all expanded asset ids once and maps usages", async () => {
    const requests: string[][] = [];
    server.use(
      http.get(
        "http://localhost:3000/api/v1/projects/demo/assets/references",
        ({ request }) => {
          const ids = new URL(request.url).searchParams.getAll("ids");
          requests.push(ids);
          return HttpResponse.json({
            ok: true,
            data: {
              usages: Object.fromEntries(
                ids.map((id, index) => [
                  id,
                  [{ episode: 1, beat_number: index + 1 }],
                ]),
              ),
              scene_co_occurrence: {},
            },
          });
        },
      ),
    );
    const refs: AssetRef[] = [
      { type: "prop", id: "red wine glass" },
      { type: "scene", id: "New York Office" },
    ];

    const { result } = renderHook(
      () => useAssetReferences("demo", refs, { enabled: true }),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(requests).toEqual([
      ["prop:red wine glass", "scene:New York Office"],
    ]);
    expect(result.current.referencesFor("prop", "red wine glass")).toEqual([
      { episode: 1, beatNumber: 1 },
    ]);
  });

  it("exposes aggregate request failures instead of treating usages as zero", async () => {
    let requests = 0;
    server.use(
      http.get(
        "http://localhost:3000/api/v1/projects/demo/assets/references",
        () => {
          requests += 1;
          return HttpResponse.json(
            { error: "reference backend down" },
            { status: 500 },
          );
        },
      ),
    );

    const { result } = renderHook(
      () =>
        useAssetReferences(
          "demo",
          [{ type: "prop", id: "Moon Fan" }],
          { enabled: true },
        ),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
    expect(requests).toBe(1);
  });
  it("does not request references while the detail surface is collapsed", async () => {
    let calls = 0;
    server.use(
      http.get(
        "http://localhost:3000/api/v1/projects/demo/assets/references",
        () => {
          calls += 1;
          return HttpResponse.json({
            ok: true,
            data: { usages: {}, scene_co_occurrence: {} },
          });
        },
      ),
    );

    const { result } = renderHook(
      () =>
        useAssetReferences(
          "demo",
          [{ type: "identity", id: "林昭_青年" }],
          { enabled: false },
        ),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(calls).toBe(0);
  });
});
