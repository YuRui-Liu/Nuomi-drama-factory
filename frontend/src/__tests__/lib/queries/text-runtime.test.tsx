import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import ky from "ky";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({ api: ky.create({ baseUrl: "http://localhost:3000/" }) }));

import { useSaveTextRuntimeConfig, useTextRuntimeConfig } from "@/lib/queries/model-gateway";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

it("loads and saves the text runtime config", async () => {
  let received: unknown;
  server.use(
    http.get("http://localhost:3000/api/v1/model-gateway/text-runtime/config", () =>
      HttpResponse.json({ ok: true, data: { source: "database", provider: "deepseek", baseUrl: "https://api.deepseek.com", model: "deepseek-v4-flash", configured: true, apiKeyConfigured: true, apiKeyPreview: "sk-***" } }),
    ),
    http.post("http://localhost:3000/api/v1/model-gateway/text-runtime/config", async ({ request }) => {
      received = await request.json();
      return HttpResponse.json({ ok: true, data: { source: "database", provider: "dramaclaw", baseUrl: "https://relayclaw.cdnfg.com/v1", model: "DC-scene-builder-LLM", configured: true, apiKeyConfigured: true, apiKeyPreview: "dc-***" } });
    }),
  );
  const loaded = renderHook(() => useTextRuntimeConfig(), { wrapper });
  await waitFor(() => expect(loaded.result.current.data?.data.provider).toBe("deepseek"));
  const saved = renderHook(() => useSaveTextRuntimeConfig(), { wrapper });
  saved.result.current.mutate({ provider: "dramaclaw", baseUrl: "https://relayclaw.cdnfg.com/v1", model: "DC-scene-builder-LLM" });
  await waitFor(() => expect(saved.result.current.isSuccess).toBe(true));
  expect(received).toEqual({ provider: "dramaclaw", baseUrl: "https://relayclaw.cdnfg.com/v1", model: "DC-scene-builder-LLM" });
});
