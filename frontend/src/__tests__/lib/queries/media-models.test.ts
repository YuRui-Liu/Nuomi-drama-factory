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
  resolveVideoModel,
  resolveVideoMode,
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

    expect(resolveVideoMode("auto", future)).toBe("i2va");
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
    expect(payloads[0]).toMatchObject({
      video_model: "runninghub:minimax-h3",
      h3_mode: "auto",
      narrative_sketch_provider: "grsai-main",
      narrative_sketch_model: "nano-banana-2",
      narrative_render_provider: "grsai-main",
      narrative_render_model: "gpt-image-2",
      narrative_render_image_size: "1K",
    });
    expect(payloads[1]).toMatchObject({
      video_workflow_parameters: {
        "runninghub:minimax-h3": { resolution: "1080p" },
      },
    });
  });
});
