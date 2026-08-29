// SPDX-License-Identifier: Elastic-2.0
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import ky from "ky";
import type { ReactNode } from "react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
vi.mock("@/lib/api", () => ({ api: ky.create({ baseUrl: "http://localhost:3000/" }) }));
import { useAssetOrganization } from "@/lib/queries/asset-organization";
const server = setupServer(); beforeAll(() => server.listen()); afterAll(() => server.close());
const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
describe("unfiled organization filter", () => {
  it("uses an explicit unfiled flag instead of an empty folder id", async () => {
    let url = ""; server.use(http.get("http://localhost:3000/api/v1/projects/demo/asset-organization", ({ request }) => { url = request.url; return HttpResponse.json({ ok: true, data: { placements: [] } }); }));
    const { result } = renderHook(() => useAssetOrganization("demo", { folderId: null }), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true)); expect(url).toContain("unfiled=true"); expect(url).not.toContain("folder_id=");
  });
});
