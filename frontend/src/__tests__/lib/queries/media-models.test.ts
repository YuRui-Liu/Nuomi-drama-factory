import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import ky from "ky";
import { createElement, type ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/" }),
}));

import {
  availableVideoModels,
  effectiveVideoMode,
  h3ModeAvailability,
  h3ModeAvailabilities,
  resolveAutomaticH3Mode,
  resolveVideoModel,
  resolveVideoMode,
  useVideoModels,
  useUpdateMediaDefaults,
  videoModelRequest,
  type VideoModelCatalogItem,
  type VideoWorkflowParameterValues,
} from "@/lib/queries/media-models";
import { queryKeys } from "@/lib/query-keys";

const server = setupServer();

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return createElement(QueryClientProvider, { client }, children);
}

describe("video media model contract", () => {
  it.each([
    { references: 1, first: true, last: true, expected: "ref2va" },
    { references: 0, first: true, last: true, expected: "fl2va" },
    { references: 0, first: true, last: false, expected: "i2va" },
    { references: 0, first: false, last: true, expected: "l2va" },
    { references: 0, first: false, last: false, expected: "t2va" },
  ] as const)(
    "resolves auto from frozen inputs: refs=$references first=$first last=$last",
    ({ references, first, last, expected }) => {
      expect(resolveAutomaticH3Mode({
        hasFirstFrame: first,
        hasLastFrame: last,
        referenceCount: references,
      })).toBe(expected);
    },
  );

  it("prioritizes input readiness, workflow verification, then model capability", () => {
    const unverified: VideoModelCatalogItem = {
      id: "runninghub:minimax-h3",
      label: "H3",
      provider: "runninghub",
      available: false,
      unavailable_reason: "profile_invalid",
      supported_modes: ["i2va"],
      default_mode: "auto",
      parameters: [],
    };

    expect(h3ModeAvailabilities(unverified, {
      hasFirstFrame: false,
      hasLastFrame: false,
      referenceCount: 0,
    }).find((item) => item.mode === "i2va")?.reason).toBe("missing_input");
    expect(h3ModeAvailabilities(unverified, {
      hasFirstFrame: true,
      hasLastFrame: false,
      referenceCount: 0,
    }).find((item) => item.mode === "i2va")?.reason).toBe("workflow_unverified");
    expect(h3ModeAvailabilities({ ...unverified, available: true, unavailable_reason: null }, {
      hasFirstFrame: false,
      hasLastFrame: true,
      referenceCount: 0,
    }).find((item) => item.mode === "l2va")?.reason).toBe("model_unsupported");
  });

  it("keeps auto selectable when registry capabilities contain resolved modes only", () => {
    const model: VideoModelCatalogItem = {
      id: "runninghub:minimax-h3",
      label: "H3",
      provider: "runninghub",
      available: true,
      supported_modes: ["i2va", "fl2va"],
      default_mode: "auto",
      parameters: [],
    };

    const automatic = h3ModeAvailabilities(model, {
      hasFirstFrame: true,
      hasLastFrame: false,
      referenceCount: 0,
    })[0];

    expect(automatic).toMatchObject({
      mode: "auto",
      resolvedMode: "i2va",
      available: true,
    });
  });

  it("prefers authoritative API mode capabilities over supported-modes fallback", () => {
    const model: VideoModelCatalogItem = {
      id: "runninghub:minimax-h3",
      label: "H3",
      provider: "runninghub",
      available: true,
      supported_modes: ["i2va", "fl2va"],
      default_mode: "auto",
      parameters: [],
      mode_capabilities: [
        {
          mode: "t2va",
          enabled: false,
          reason: "workflow_capability_unverified",
          requires_first_frame: false,
          requires_last_frame: false,
          requires_references: false,
        },
        {
          mode: "ref2va",
          enabled: false,
          reason: "hybrid_input_unverified",
          requires_first_frame: false,
          requires_last_frame: false,
          requires_references: true,
        },
      ],
    };

    expect(h3ModeAvailability(model, {
      hasFirstFrame: false,
      hasLastFrame: false,
      referenceCount: 0,
    }, "t2va")).toMatchObject({
      available: false,
      reason: "workflow_capability_unverified",
    });
    expect(h3ModeAvailability(model, {
      hasFirstFrame: false,
      hasLastFrame: false,
      referenceCount: 1,
    }, "ref2va")).toMatchObject({
      available: false,
      reason: "hybrid_input_unverified",
    });
  });

  it("keeps supported_modes as the legacy API fallback", () => {
    const legacy: VideoModelCatalogItem = {
      id: "runninghub:minimax-h3",
      label: "H3",
      provider: "runninghub",
      available: true,
      supported_modes: ["i2va", "fl2va"],
      default_mode: "auto",
      parameters: [],
    };

    expect(h3ModeAvailability(legacy, {
      hasFirstFrame: false,
      hasLastFrame: true,
      referenceCount: 0,
    }, "l2va").reason).toBe("model_unsupported");
  });

  it("allows reference mode to carry frozen transport frames in explicit and auto selection", () => {
    const model: VideoModelCatalogItem = {
      id: "runninghub:minimax-h3-ref",
      label: "H3 Ref",
      provider: "runninghub",
      available: true,
      supported_modes: ["ref2va"],
      default_mode: "auto",
      parameters: [],
      mode_capabilities: [{
        mode: "ref2va",
        enabled: true,
        reason: null,
        requires_first_frame: false,
        requires_last_frame: false,
        requires_references: true,
      }],
    };
    const inputs = {
      hasFirstFrame: true,
      hasLastFrame: true,
      referenceCount: 2,
    };

    expect(h3ModeAvailability(model, inputs, "auto")).toMatchObject({
      resolvedMode: "ref2va",
      available: true,
    });
    expect(h3ModeAvailability(model, inputs, "ref2va")).toMatchObject({
      resolvedMode: "ref2va",
      available: true,
    });
  });

  it("selects FL2VA for first and last frames and I2VA for first frame only", () => {
    expect(effectiveVideoMode("auto", true, true)).toBe("fl2va");
    expect(effectiveVideoMode("auto", true, false)).toBe("i2va");
    expect(effectiveVideoMode("fl2va", true, false)).toBe("fl2va");
  });

  it.each(["newapi_seedance-1.0-pro-fast", "unknown:video-model"])(
    "falls back from saved model %s to the only available workflow",
    (savedModel) => {
      const catalog: VideoModelCatalogItem[] = [
        { id: "runninghub:disabled", label: "Disabled", provider: "runninghub", available: false, supported_modes: ["auto"], default_mode: "auto", parameters: [] },
        { id: "runninghub:minimax-h3", label: "RunningHub MiniMax H3", provider: "runninghub", available: true, supported_modes: ["auto", "i2va", "fl2va"], default_mode: "auto", parameters: [] },
      ];

      expect(availableVideoModels(catalog).map((item) => item.id)).toEqual(["runninghub:minimax-h3"]);
      expect(resolveVideoModel(savedModel, catalog)?.id).toBe("runninghub:minimax-h3");
    },
  );

  it("never resolves an unavailable saved workflow", () => {
    const catalog: VideoModelCatalogItem[] = [
      { id: "runninghub:minimax-h3", label: "RunningHub MiniMax H3", provider: "runninghub", available: false, supported_modes: ["auto"], default_mode: "auto", parameters: [] },
      { id: "runninghub:future", label: "Future Workflow", provider: "runninghub", available: true, supported_modes: ["auto"], default_mode: "auto", parameters: [] },
    ];

    expect(resolveVideoModel("runninghub:minimax-h3", catalog)?.id).toBe("runninghub:future");
  });

  it("falls back unsupported saved modes to the workflow default and preserves supported modes", () => {
    const future: VideoModelCatalogItem = {
      id: "runninghub:future",
      label: "Future Workflow",
      provider: "runninghub",
      available: true,
      supported_modes: ["i2va"],
      default_mode: "i2va",
      parameters: [],
    };
    const h3: VideoModelCatalogItem = {
      ...future,
      id: "runninghub:minimax-h3",
      supported_modes: ["auto", "i2va", "fl2va"],
      default_mode: "auto",
    };

    expect(resolveVideoMode("auto", future)).toBe("auto");
    expect(resolveVideoMode("i2va", future)).toBe("i2va");
    expect(resolveVideoMode("auto", h3)).toBe("auto");
  });

  it("serializes only stable model identifiers and mode", () => {
    expect(videoModelRequest("runninghub:minimax-h3", "auto")).toEqual({
      video_model: "runninghub:minimax-h3",
      h3_mode: "auto",
    });
    expect(queryKeys.videoModels()).toEqual(["media-capabilities", "video", "models"]);
  });

  it("exposes public workflow parameter definitions and values", () => {
    const values: VideoWorkflowParameterValues = {
      "runninghub:minimax-h3": { resolution: "720p" },
    };
    const model: VideoModelCatalogItem = {
      id: "runninghub:minimax-h3",
      label: "RunningHub MiniMax H3",
      provider: "runninghub",
      available: true,
      supported_modes: ["auto", "i2va", "fl2va"],
      default_mode: "auto",
      parameters: [
        {
          key: "resolution",
          type: "enum",
          label: "分辨率",
          description: "",
          default: "720p",
          scope: "narrative_group",
          options: [
            {
              value: "720p",
              label: "标准",
              description: "",
              relative_cost: "standard",
            },
          ],
        },
      ],
    };

    expect(model.parameters[0]?.default).toBe(values[model.id]?.resolution);
  });

  it("parses an optional video reference policy while keeping legacy models compatible", async () => {
    server.use(http.get(
      "http://localhost:3000/api/v1/media-capabilities/video/models",
      () => HttpResponse.json({
        ok: true,
        data: [
          {
            id: "runninghub:minimax-h3-ref",
            label: "MiniMax H3 Ref",
            provider: "runninghub",
            available: true,
            supported_modes: ["auto"],
            default_mode: "auto",
            parameters: [],
            reference_policy: {
              required: true,
              min_images: 1,
              max_images: 5,
              source_kinds: ["character_identity", "scene_master", "prop_reference", "temporary_upload"],
            },
          },
          {
            id: "runninghub:minimax-h3",
            label: "MiniMax H3",
            provider: "runninghub",
            available: true,
            supported_modes: ["auto"],
            default_mode: "auto",
            parameters: [],
          },
        ],
      }),
    ));

    const { result } = renderHook(() => useVideoModels(), { wrapper });
    await waitFor(() => expect(result.current.data?.ok).toBe(true));
    if (!result.current.data?.ok) throw new Error("catalog did not load");

    expect(result.current.data.data[0].reference_policy).toEqual({
      required: true,
      min_images: 1,
      max_images: 5,
      source_kinds: ["character_identity", "scene_master", "prop_reference", "temporary_upload"],
    });
    expect(result.current.data.data[1].reference_policy).toBeUndefined();
  });

  it("sends workflow parameters only when the mutation input provides them", async () => {
    const payloads: unknown[] = [];
    server.use(
      http.put(
        "http://localhost:3000/api/v1/projects/demo/media-defaults",
        async ({ request }) => {
          payloads.push(await request.json());
          return HttpResponse.json({ ok: true, data: {} });
        },
      ),
    );

    const omitted = renderHook(() => useUpdateMediaDefaults("demo"), { wrapper });
    omitted.result.current.mutate({ videoModel: "runninghub:minimax-h3" });
    await waitFor(() => expect(omitted.result.current.isSuccess).toBe(true));

    const provided = renderHook(() => useUpdateMediaDefaults("demo"), { wrapper });
    provided.result.current.mutate({
      videoModel: "runninghub:minimax-h3",
      videoWorkflowParameters: {
        "runninghub:minimax-h3": { resolution: "1080p" },
      },
    });
    await waitFor(() => expect(provided.result.current.isSuccess).toBe(true));

    expect(payloads[0]).not.toHaveProperty("video_workflow_parameters");
    expect(payloads[0]).toEqual({
      video_model: "runninghub:minimax-h3",
      h3_mode: "auto",
    });
    expect(payloads[1]).toMatchObject({
      video_workflow_parameters: {
        "runninghub:minimax-h3": { resolution: "1080p" },
      },
    });
  });
});
