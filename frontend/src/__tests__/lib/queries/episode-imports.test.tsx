// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import ky from "ky";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/" }),
}));

import {
  useCommitEpisodeImport,
  useClearEpisodeImportStale,
  useEpisodeImports,
  usePreviewEpisodeImports,
} from "@/lib/queries/ingest";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";

const server = setupServer();

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapperWithClient(queryClient: QueryClient) {
  return function TestWrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  };
}

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("episode import queries", () => {
  it("sends every selected file in one preview request", async () => {
    server.use(
      http.post(
        "http://localhost:3000/api/v1/projects/demo/episode-imports/preview",
        () => {
          return HttpResponse.json({
            ok: true,
            data: { preview_id: "p1", base_revision: 4, files: [] },
          });
        },
      ),
    );
    const postSpy = vi.spyOn(api, "post");
    const { result } = renderHook(() => usePreviewEpisodeImports("demo"), {
      wrapper: wrapperWithClient(client()),
    });

    const response = await result.current.mutateAsync([
      new File(["two"], "E02.md", { type: "text/markdown" }),
      new File(["three"], "E03.md", { type: "text/markdown" }),
    ]);

    expect(response.data.files).toEqual([]);
    const options = postSpy.mock.calls[0][1] as { body: FormData };
    expect(options.body.getAll("files")).toHaveLength(2);
    expect(options.body.get("input_intent")).toBe("existing_script");
    expect((options.body.getAll("files")[0] as File).name).toBe("E02.md");
    postSpy.mockRestore();
  });

  it("commits explicit overwrite and skip resolutions and invalidates dependent data", async () => {
    let receivedJson: unknown;
    server.use(
      http.post(
        "http://localhost:3000/api/v1/projects/demo/episode-imports/commit",
        async ({ request }) => {
          receivedJson = await request.json();
          return HttpResponse.json({
            ok: true,
            task_type: "episode_import_commit",
            message: "started",
          });
        },
      ),
    );
    const queryClient = client();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useCommitEpisodeImport("demo"), {
      wrapper: wrapperWithClient(queryClient),
    });
    const body = {
      preview_id: "p1",
      expected_revision: 4,
      resolutions: [
        { file_id: "a", episode_number: 2, action: "overwrite" as const },
        { file_id: "b", episode_number: 3, action: "skip" as const },
      ],
    };

    await result.current.mutateAsync(body);

    expect(receivedJson).toEqual(body);
    for (const queryKey of [
      queryKeys.episodeImports("demo"),
      queryKeys.chapters("demo"),
      queryKeys.episodes("demo"),
      queryKeys.knowledgeGraph("demo"),
      queryKeys.tasks("demo"),
      queryKeys.pipelineStatus("demo"),
    ]) {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey });
    }
    expect(queryClient.getQueryData(queryKeys.chapters("demo"))).toBeUndefined();
  });

  it("lists existing imports under the project-scoped key", async () => {
    server.use(
      http.get(
        "http://localhost:3000/api/v1/projects/demo/episode-imports",
        () =>
          HttpResponse.json({
            ok: true,
            data: {
              project_revision: 4,
              items: [{
                episode_number: 2,
                title: "第二集",
                content_hash: "sha256",
                filename: "E02.md",
                revision: 3,
                downstream_stale: false,
                imported_at: "2026-08-17T00:00:00Z",
                updated_at: "2026-08-17T00:00:00Z",
              }],
            },
          }),
      ),
    );
    const queryClient = client();
    const { result } = renderHook(() => useEpisodeImports("demo"), {
      wrapper: wrapperWithClient(queryClient),
    });

    await waitFor(() => expect(result.current.data?.ok).toBe(true));
    expect(result.current.data?.data.project_revision).toBe(4);
    expect(result.current.data?.data.items[0]).toMatchObject({
      filename: "E02.md",
      revision: 3,
    });
    expect(queryClient.getQueryData(queryKeys.episodeImports("demo"))).toEqual(
      result.current.data,
    );
  });

  it("preserves the backend message for a 409 conflict", async () => {
    server.use(
      http.post(
        "http://localhost:3000/api/v1/projects/demo/episode-imports/commit",
        () =>
          HttpResponse.json(
            {
              ok: false,
              code: "EPISODE_IMPORT_REVISION_CONFLICT",
              error: "项目内容已变化，请重新预检",
            },
            { status: 409 },
          ),
      ),
    );
    const { result } = renderHook(() => useCommitEpisodeImport("demo"), {
      wrapper: wrapperWithClient(client()),
    });

    await expect(
      result.current.mutateAsync({
        preview_id: "p1",
        expected_revision: 4,
        resolutions: [],
      }),
    ).rejects.toThrow("项目内容已变化，请重新预检");
  });

  it("preserves FastAPI detail error codes and messages", async () => {
    server.use(
      http.post("http://localhost:3000/api/v1/projects/demo/episode-imports/commit", () =>
        HttpResponse.json(
          { detail: { code: "EPISODE_IMPORT_INVALID_RESOLUTION", error: "新增文件动作必须为 import" } },
          { status: 409 },
        ),
      ),
    );
    const { result } = renderHook(() => useCommitEpisodeImport("demo"), {
      wrapper: wrapperWithClient(client()),
    });

    await expect(result.current.mutateAsync({
      preview_id: "p1", expected_revision: 4, resolutions: [],
    })).rejects.toMatchObject({
      message: "新增文件动作必须为 import",
      body: { detail: { code: "EPISODE_IMPORT_INVALID_RESOLUTION" } },
    });
  });

  it("clears one stale stage and refreshes the episode import list", async () => {
    server.use(
      http.delete("http://localhost:3000/api/v1/projects/demo/episode-imports/stale/2/beats", ({ request }) => {
        expect(new URL(request.url).searchParams.get("source_revision")).toBe("7");
        return HttpResponse.json({ ok: true, data: { cleared: true } });
      }),
    );
    const queryClient = client();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useClearEpisodeImportStale("demo"), {
      wrapper: wrapperWithClient(queryClient),
    });

    await expect(result.current.mutateAsync({ episodeNumber: 2, stage: "beats", sourceRevision: 7 }))
      .resolves.toMatchObject({ data: { cleared: true } });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: queryKeys.episodeImports("demo") });
  });
});
