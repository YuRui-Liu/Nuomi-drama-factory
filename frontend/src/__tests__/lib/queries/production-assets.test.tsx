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

import { queryKeys } from "@/lib/query-keys";
import {
  useAdoptProductionAssetVersion,
  useProductionAssetSlot,
  type ProductionAssetGenerationMetadata,
  type ProductionAssetQc,
  type ProductionAssetSlotData,
  type ProductionAssetVersion,
} from "@/lib/queries/production-assets";

const server = setupServer();

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapperWithClient(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  };
}

const generationMetadata: ProductionAssetGenerationMetadata = {
  provider: "grsai",
  model: "image-v2",
  panel_layout: ["front", "side", "back"],
};

const qc: ProductionAssetQc = {
  qc_passed: true,
  soft_issues: ["发丝轻微闪烁"],
  technical_error: null,
};

const currentVersion: ProductionAssetVersion = {
  version_id: "state-v1",
  slot_id: "character:lin:state:duty",
  source_attempt_id: "attempt-1",
  asset_path: "assets/characters/lin/identities/duty.png",
  origin: "generated",
  generation_metadata: generationMetadata,
  adoption_status: "provisional",
  ...qc,
  created_at: "2026-09-01T08:00:00Z",
};

const slotData: ProductionAssetSlotData = {
  slot: {
    slot_id: "character:lin:state:duty",
    asset_kind: "character_state",
    critical: true,
    current_version_id: "state-v1",
    version_ids: ["state-v1"],
  },
  versions: [currentVersion],
  current_version: currentVersion,
  read_only: false,
  read_only_reason: null,
};

describe("production asset query hooks", () => {
  it("loads a typed slot with asset kind and legacy asset path", async () => {
    let requestedPath = "";
    let requestedAssetKind: string | null = null;
    let requestedLegacyPath: string | null = null;
    server.use(
      http.get(
        "http://localhost:3000/api/v1/projects/demo/production-assets/slots/*",
        ({ request }) => {
          const url = new URL(request.url);
          requestedPath = url.pathname;
          requestedAssetKind = url.searchParams.get("asset_kind");
          requestedLegacyPath = url.searchParams.get("legacy_asset_path");
          return HttpResponse.json({ ok: true, data: slotData });
        },
      ),
    );

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const legacyAssetPath = "assets/characters/lin/identities/duty.png";
    const { result } = renderHook(
      () =>
        useProductionAssetSlot(
          "demo",
          "character:lin:state:duty",
          "character_state",
          legacyAssetPath,
        ),
      { wrapper: wrapperWithClient(queryClient) },
    );

    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(requestedPath).toBe(
      "/api/v1/projects/demo/production-assets/slots/character%3Alin%3Astate%3Aduty",
    );
    expect(requestedAssetKind).toBe("character_state");
    expect(requestedLegacyPath).toBe(legacyAssetPath);
    expect(result.current.data?.data).toEqual(slotData);
    expect(result.current.data?.data.current_version?.generation_metadata).toEqual(
      generationMetadata,
    );
    expect(
      queryClient.getQueryData([
        ...queryKeys.productionAssetSlot("demo", "character:lin:state:duty"),
        "character_state",
        legacyAssetPath,
      ]),
    ).toEqual({ ok: true, data: slotData });
  });

  it("omits legacy_asset_path when no legacy asset is provided", async () => {
    let hasLegacyPath = true;
    server.use(
      http.get(
        "http://localhost:3000/api/v1/projects/demo/production-assets/slots/*",
        ({ request }) => {
          hasLegacyPath = new URL(request.url).searchParams.has(
            "legacy_asset_path",
          );
          return HttpResponse.json({ ok: true, data: slotData });
        },
      ),
    );

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(
      () =>
        useProductionAssetSlot(
          "demo",
          "character:lin:state:duty",
          "character_state",
        ),
      { wrapper: wrapperWithClient(queryClient) },
    );

    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(hasLegacyPath).toBe(false);
  });

  it("adopts a version and invalidates every query for the slot", async () => {
    let requestedPath = "";
    let requestedBody: unknown = null;
    server.use(
      http.post(
        "http://localhost:3000/api/v1/projects/demo/production-assets/slots/*",
        async ({ request }) => {
          requestedPath = new URL(request.url).pathname;
          requestedBody = await request.clone().json();
          return HttpResponse.json({
            ok: true,
            data: {
              slot: { ...slotData.slot, current_version_id: "candidate-2" },
              versions: slotData.versions,
              event: {
                slot_id: slotData.slot.slot_id,
                version_id: "candidate-2",
                from_status: "candidate",
                to_status: "adopted",
                actor: "frank",
                at: "2026-09-01T08:30:00Z",
                reason: "三视图一致性更好",
                source_attempt_id: "attempt-2",
              },
            },
          });
        },
      ),
    );

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(
      () =>
        useAdoptProductionAssetVersion(
          "demo",
          "character:lin:state:duty",
        ),
      { wrapper: wrapperWithClient(queryClient) },
    );

    const response = await result.current.mutateAsync({
      versionId: "candidate-2",
      reason: "三视图一致性更好",
    });

    expect(requestedPath).toBe(
      "/api/v1/projects/demo/production-assets/slots/character%3Alin%3Astate%3Aduty/versions/candidate-2/adopt",
    );
    expect(requestedBody).toEqual({ reason: "三视图一致性更好" });
    expect(response.data.event.to_status).toBe("adopted");
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: queryKeys.productionAssetSlot(
        "demo",
        "character:lin:state:duty",
      ),
    });
  });
});
