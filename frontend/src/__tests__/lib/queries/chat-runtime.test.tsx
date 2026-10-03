import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import ky from "ky";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({ api: ky.create({ baseUrl: "http://localhost:3000/" }) }));
import { useChatRuntimeConfig, useSaveChatRuntimeConfig } from "@/lib/queries/chat-runtime";
const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

it("loads and saves chat settings on their own API endpoint", async () => {
  let received: unknown;
  const config = { backend: "deepseek_harness" as const, model: "deepseek-v4-flash-vision-exp", reasoningEffort: "low" as const };
  server.use(
    http.get("http://localhost:3000/api/v1/chat-runtime/config", () => HttpResponse.json({ ok: true, data: config })),
    http.put("http://localhost:3000/api/v1/chat-runtime/config", async ({ request }) => {
      received = await request.json();
      return HttpResponse.json({ ok: true, data: config });
    }),
  );
  const loaded = renderHook(() => useChatRuntimeConfig(), { wrapper });
  await waitFor(() => expect(loaded.result.current.data?.data).toEqual(config));
  const saved = renderHook(() => useSaveChatRuntimeConfig(), { wrapper });
  saved.result.current.mutate(config);
  await waitFor(() => expect(saved.result.current.isSuccess).toBe(true));
  expect(received).toEqual(config);
});
