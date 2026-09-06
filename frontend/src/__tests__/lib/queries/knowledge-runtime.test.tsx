import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import ky from "ky";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/" }),
}));

import { api } from "@/lib/api";
import {
  useKnowledgeRuntimeStatus,
  useMediaProviderAccounts,
  useRecognizeCodex,
  useRunningHubWorkflows,
  useSaveMediaProviderAccount,
  useSaveRunningHubWorkflows,
  useSaveKnowledgeRuntimeSettings,
} from "@/lib/queries/knowledge-runtime";

const server = setupServer();

beforeAll(() => server.listen());
afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
});
afterAll(() => server.close());

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

it("loads the independent Codex and Ollama runtime status", async () => {
  server.use(
    http.get("http://localhost:3000/api/v1/knowledge-runtime/status", () =>
      HttpResponse.json({
        ok: true,
        data: {
          ready: true,
          state: "ready",
          message: "ready",
          codex: {
            installed: true,
            compatible: true,
            authenticated: true,
            ready: true,
            path: "E:\\Tools\\Codex\\codex.exe",
            version: "1.2.3",
            message: "ok",
            state: "ready",
          },
          ollama: {
            provider: "ollama",
            baseUrl: "http://127.0.0.1:11434",
            model: "bge-m3:latest",
            dimension: 1024,
            digest: "sha256:def",
            batchSize: 8,
            probedAt: "now",
            configured: true,
          },
        },
      }),
    ),
  );

  const { result } = renderHook(() => useKnowledgeRuntimeStatus(), { wrapper });
  await waitFor(() => expect(result.current.data?.ready).toBe(true));
  expect(result.current.data?.codex.authenticated).toBe(true);
});

it("recognizes Codex once and invalidates the runtime status query after success", async () => {
  server.use(
    http.post("http://localhost:3000/api/v1/knowledge-runtime/codex/test", () => {
      return HttpResponse.json({
        ok: true,
        data: {
          installed: true,
          compatible: true,
          authenticated: true,
          ready: true,
          path: "E:\\Tools\\Codex\\codex.exe",
          version: "1.2.3",
          message: "Codex CLI 可用",
          state: "ready",
        },
      });
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const post = vi.spyOn(api, "post");
  const invalidateQueries = vi.spyOn(client, "invalidateQueries");
  const recognizeWrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );

  const { result } = renderHook(() => useRecognizeCodex(), { wrapper: recognizeWrapper });
  result.current.mutate();

  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(post).toHaveBeenCalledTimes(1);
  expect(post).toHaveBeenCalledWith("api/v1/knowledge-runtime/codex/test", {
    throwHttpErrors: false,
  });
  expect(result.current.data).toMatchObject({
    ok: true,
    data: { ready: true, path: "E:\\Tools\\Codex\\codex.exe" },
  });
  expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["knowledge-runtime"] });
});

it("saves Ollama settings through the probe-enforcing endpoint", async () => {
  let received: unknown;
  server.use(
    http.put("http://localhost:3000/api/v1/knowledge-runtime/settings", async ({ request }) => {
      received = await request.json();
      return HttpResponse.json({
        ok: true,
        data: {
          provider: "ollama",
          baseUrl: "http://127.0.0.1:11434",
          model: "bge-m3:latest",
          dimension: 1024,
          digest: "sha256:def",
          batchSize: 8,
          probedAt: "now",
          configured: true,
        },
      });
    }),
  );

  const { result } = renderHook(() => useSaveKnowledgeRuntimeSettings(), { wrapper });
  result.current.mutate({
    baseUrl: "http://127.0.0.1:11434",
    model: "bge-m3:latest",
    batchSize: 8,
  });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(received).toEqual({
    baseUrl: "http://127.0.0.1:11434",
    model: "bge-m3:latest",
    batchSize: 8,
  });
});

it("loads existing GRSAI and RunningHub provider accounts", async () => {
  server.use(
    http.get("http://localhost:3000/api/v1/media-capabilities/providers", () =>
      HttpResponse.json([
        {
          id: "grsai-main",
          provider_type: "grsai",
          base_url: "https://grsai.example",
          enabled: true,
          max_concurrency: 3,
          poll_concurrency: 6,
          queue_limit: 30,
          capability_limits: {},
          credential_configured: true,
          credential_scheme: "env",
        },
      ]),
    ),
  );

  const { result } = renderHook(() => useMediaProviderAccounts(), { wrapper });
  await waitFor(() => expect(result.current.data).toHaveLength(1));
  expect(result.current.data?.[0].provider_type).toBe("grsai");
});

it("loads and saves the supported RunningHub workflow IDs", async () => {
  let received: unknown;
  const workflows = {
    image_upscale: "1001",
    video_minimax_h3: "2087934731806658562",
    video_minimax_h3_ref: "2096502793044582401",
    video_minimax_h3_ref_max_images: 5,
    tts_qwen3_voice_design: "3003",
    tts_indextts2_voice_clone: "4004",
  };
  server.use(
    http.get(
      "http://localhost:3000/api/v1/media-capabilities/providers/runninghub-main/workflows",
      () => HttpResponse.json(workflows),
    ),
    http.put(
      "http://localhost:3000/api/v1/media-capabilities/providers/runninghub-main/workflows",
      async ({ request }) => {
        received = await request.json();
        return HttpResponse.json(workflows);
      },
    ),
  );

  const loaded = renderHook(() => useRunningHubWorkflows(), { wrapper });
  await waitFor(() => expect(loaded.result.current.data).toEqual(workflows));

  const saved = renderHook(() => useSaveRunningHubWorkflows(), { wrapper });
  saved.result.current.mutate(workflows);
  await waitFor(() => expect(saved.result.current.isSuccess).toBe(true));
  expect(received).toEqual(workflows);
});

it("saves provider, API key and workflows through one settings request", async () => {
  let received: unknown;
  server.use(
    http.put(
      "http://localhost:3000/api/v1/media-capabilities/providers/runninghub-main/settings",
      async ({ request }) => {
        received = await request.json();
        return HttpResponse.json({
          provider: {
            id: "runninghub-main",
            provider_type: "runninghub",
            base_url: "https://www.runninghub.cn",
            enabled: true,
            max_concurrency: 5,
            poll_concurrency: 10,
            queue_limit: 100,
            capability_limits: {},
            credential_configured: true,
            credential_scheme: "keyring",
          },
          workflows: null,
        });
      },
    ),
  );
  const { result } = renderHook(() => useSaveMediaProviderAccount(), { wrapper });
  result.current.mutate({
    id: "runninghub-main",
    provider_type: "runninghub",
    base_url: "https://www.runninghub.cn",
    api_key: "rh-secret",
    max_concurrency: 5,
    poll_concurrency: 10,
    queue_limit: 100,
  });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(received).toMatchObject({
    provider_type: "runninghub",
    api_key: "rh-secret",
    workflows: null,
  });
});
