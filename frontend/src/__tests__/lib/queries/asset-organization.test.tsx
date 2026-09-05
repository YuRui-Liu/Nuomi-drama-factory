// SPDX-License-Identifier: Elastic-2.0
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

import {
  useAssetFolders,
  useAssetOrganization,
  useCreateAssetFolder,
  useDeleteAssetFolder,
  useOrganizeAsset,
  useRenameAssetFolder,
} from "@/lib/queries/asset-organization";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {children}
    </QueryClientProvider>
  );
}

describe("asset organization query contract", () => {
  it("loads folders and combines folder with purpose filters", async () => {
    let organizationUrl = "";
    server.use(
      http.get("http://localhost:3000/api/v1/projects/demo/asset-folders", () =>
        HttpResponse.json({ ok: true, data: { folders: [{ id: "f1", name: "人物素材", asset_count: 1 }] } }),
      ),
      http.get("http://localhost:3000/api/v1/projects/demo/asset-organization", ({ request }) => {
        organizationUrl = request.url;
        return HttpResponse.json({ ok: true, data: { placements: [] } });
      }),
    );

    const folders = renderHook(() => useAssetFolders("demo"), { wrapper });
    const placements = renderHook(
      () => useAssetOrganization("demo", { folderId: "f1", purpose: "character" }),
      { wrapper },
    );

    await waitFor(() => expect(folders.result.current.isSuccess).toBe(true));
    await waitFor(() => expect(placements.result.current.isSuccess).toBe(true));
    expect(folders.result.current.data?.data.folders[0]?.name).toBe("人物素材");
    expect(organizationUrl).toContain("folder_id=f1");
    expect(organizationUrl).toContain("purpose=character");
  });

  it("uses the approved CRUD and move endpoints without changing the stable asset id", async () => {
    const requests: Array<{ method: string; url: string; body?: unknown }> = [];
    server.use(
      http.post("http://localhost:3000/api/v1/projects/demo/asset-folders", async ({ request }) => {
        requests.push({ method: "POST", url: request.url, body: await request.json() });
        return HttpResponse.json({ ok: true, data: { id: "f2", name: "场景素材", asset_count: 0 } });
      }),
      http.patch("http://localhost:3000/api/v1/projects/demo/asset-folders/f2", async ({ request }) => {
        requests.push({ method: "PATCH", url: request.url, body: await request.json() });
        return HttpResponse.json({ ok: true, data: { id: "f2", name: "主场景库", asset_count: 0 } });
      }),
      http.delete("http://localhost:3000/api/v1/projects/demo/asset-folders/f2", ({ request }) => {
        requests.push({ method: "DELETE", url: request.url });
        return HttpResponse.json({ ok: true, data: { id: "f2", unfiled_count: 2 } });
      }),
      http.put("http://localhost:3000/api/v1/projects/demo/assets/image/characters%3Ahero%3Aportrait.png/organization", async ({ request }) => {
        requests.push({ method: "PUT", url: request.url, body: await request.json() });
        return HttpResponse.json({ ok: true, data: { asset_type: "image", asset_id: "characters:hero:portrait.png", folder_id: "f2", purpose: "character" } });
      }),
    );

    const create = renderHook(() => useCreateAssetFolder("demo"), { wrapper });
    const rename = renderHook(() => useRenameAssetFolder("demo"), { wrapper });
    const remove = renderHook(() => useDeleteAssetFolder("demo"), { wrapper });
    const organize = renderHook(() => useOrganizeAsset("demo"), { wrapper });

    await create.result.current.mutateAsync({ name: "场景素材" });
    await rename.result.current.mutateAsync({ folderId: "f2", name: "主场景库" });
    await organize.result.current.mutateAsync({ assetType: "image", assetId: "characters:hero:portrait.png", folder_id: "f2", purpose: "character" });
    await remove.result.current.mutateAsync({ folderId: "f2" });

    expect(requests.map((request) => request.body)).toContainEqual({ name: "场景素材" });
    expect(requests.map((request) => request.body)).toContainEqual({ name: "主场景库" });
    expect(requests.map((request) => request.body)).toContainEqual({ folder_id: "f2", purpose: "character" });
    expect(requests.find((request) => request.method === "PUT")?.url).toContain("characters%3Ahero%3Aportrait.png");
  });
});
