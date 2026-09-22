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
  narrativeGroupActionPath,
  narrativeGroupActionPayload,
  narrativeGroupVideoPath,
  narrativeGroupVideoPlanPath,
  narrativeGroupVideoPlanPayload,
  narrativeGroupVideoDialogueSourcePath,
  narrativeGroupVideoDialogueSourcePayload,
  narrativeGroupVideoPayload,
  narrativeGroupVideoReferencePreviewPath,
  narrativeGroupVideoReferencePreviewQueryKey,
  narrativeGroupVideoReferenceUploadPath,
  narrativeGroupVideoReferencesPath,
  narrativeGroupVideoTaskScope,
  narrativeGroupTaskScope,
  narrativeGroupReferencePath,
  narrativeGroupRevisionPath,
  narrativeGroupRollbackPath,
  useNarrativeGroupAction,
  useNarrativeGroupReferences,
  useNarrativeGroupVideoReferencePreview,
  useGenerateNarrativeGroupVideo,
  useGenerateNarrativeGroupVideoSegment,
  useUpdateNarrativeGroupVideoReferences,
  useUpdateNarrativeGroupVideoPlan,
  useUploadNarrativeGroupVideoReference,
  useUploadNarrativeReference,
} from "@/lib/queries/narrative-groups";
import { api } from "@/lib/api";
import { BackendStatusError } from "@/lib/api-errors";
import { queryKeys } from "@/lib/query-keys";
import type {
  PlannedNarrativeGroupGenerationSelection,
  PlannedNarrativeGroupReferencePreview,
} from "@/lib/queries/narrative-groups";

const plannedReferencePreview: PlannedNarrativeGroupReferencePreview = {
  reference_revision: "director-plan-r7",
  bindings: [{
    binding_id: "planned-hero",
    asset_kind: "character_identity",
    display_label: "主角",
    beat_ids: ["beat-1"],
    required: true,
    status: "ready",
    resolution: "auto_matched",
    selected_by_default: true,
  }],
  max_images: 9,
};

describe("narrative group query contract", () => {
  it("uses only the planned reference preview contract", () => {
    expect(plannedReferencePreview).toMatchObject({
      reference_revision: "director-plan-r7",
      max_images: 9,
      bindings: [{ binding_id: "planned-hero" }],
    });
  });

  it("builds a scoped stage action without client credentials", () => {
    expect(narrativeGroupActionPath("demo project", 2, "ng-01", "render", "regenerate"))
      .toBe("api/v1/projects/demo%20project/episodes/2/narrative-groups/ng-01/render/regenerate");
    expect(narrativeGroupActionPayload({ revision: 3, apiKey: "must-not-leak" } as never))
      .toEqual({ revision: 3 });
  });

  it("builds one director-video request with configurable aspect and resolution", () => {
    expect(narrativeGroupVideoPath("demo project", 2, "ng-01"))
      .toBe("api/v1/projects/demo%20project/episodes/2/narrative-groups/ng-01/video/generate");
    expect(narrativeGroupVideoPayload({
      model: "runninghub:minimax-h3", mode: "fl2va", revision: 4,
      planRevision: 7, referenceRevision: 3, aspectRatio: "16:9", resolution: "720p",
    })).toEqual({
      model: "runninghub:minimax-h3", mode: "fl2va", revision: 4,
      plan_revision: 7, reference_revision: 3, aspect_ratio: "16:9", resolution: "720p",
    });
  });

  it.each(["auto", "t2va", "i2va", "fl2va", "l2va", "ref2va"] as const)(
    "preserves requested H3 mode %s in the transport payload",
    (mode) => {
      expect(narrativeGroupVideoPayload({
        model: "runninghub:minimax-h3",
        mode,
        revision: 4,
        aspectRatio: "16:9",
      })).toMatchObject({ mode });
    },
  );

  it("omits reference_revision from legacy video generation requests", () => {
    expect(narrativeGroupVideoPayload({
      model: "runninghub:minimax-h3", mode: "auto", revision: 4,
      planRevision: 7, aspectRatio: "9:16",
    })).not.toHaveProperty("reference_revision");
  });

  it("builds the video-plan endpoint and serializes only ordered Beat groups", () => {
    expect(narrativeGroupVideoPlanPath("demo project", 2, "组 一"))
      .toBe("api/v1/projects/demo%20project/episodes/2/narrative-groups/%E7%BB%84%20%E4%B8%80/video/plan");
    expect(narrativeGroupVideoPlanPayload({
      expectedRevision: 3,
      units: [{ beatIds: ["beat-8", "beat-9"] }, { beatIds: ["beat-10"] }],
    })).toEqual({
      expected_revision: 3,
      units: [{ beat_ids: ["beat-8", "beat-9"] }, { beat_ids: ["beat-10"] }],
    });
  });

  it("preserves an explicit I2VA mode for a paired video unit", () => {
    expect(narrativeGroupVideoPlanPayload({
      expectedRevision: 2,
      units: [{ beatIds: ["shot-1", "shot-2"], mode: "i2va" }],
    })).toEqual({
      expected_revision: 2,
      units: [{ beat_ids: ["shot-1", "shot-2"], mode: "i2va" }],
    });
  });

  it("uses the exact server H3 scope for the current video revision", () => {
    expect(narrativeGroupVideoTaskScope("ng-01", 4)).toBe("group_ng-01_video_r4");
    expect(narrativeGroupTaskScope("ng-01", "sketch", 2)).toBe("group_ng-01_sketch_r2");
    expect(narrativeGroupTaskScope("ng-01", "render", 3)).toBe("group_ng-01_render_r3");
  });

  it("uses a separate recomposition-only dialogue source endpoint", () => {
    expect(narrativeGroupVideoDialogueSourcePath("demo", 2, "ng-01"))
      .toBe("api/v1/projects/demo/episodes/2/narrative-groups/ng-01/video/dialogue-source");
  });

  it("sends the current stage revision when switching a dialogue source", () => {
    expect(narrativeGroupVideoDialogueSourcePayload({
      spanIndex: 2,
      dialogueSource: "h3_native",
      revision: 4,
    })).toEqual({ span_index: 2, dialogue_source: "h3_native", revision: 4 });
  });

  it("builds an encoded reference-preview path", () => {
    expect(narrativeGroupReferencePath("demo project", 2, "组 一", "render"))
      .toBe("api/v1/projects/demo%20project/episodes/2/narrative-groups/%E7%BB%84%20%E4%B8%80/render/references");
  });

  it("serializes only the planned binding contract", () => {
    const plannedSelection: PlannedNarrativeGroupGenerationSelection = {
      selectedBindingIds: ["binding-2", "binding-1"],
      uploadIds: ["upload-1"],
      referenceRevision: "plan-revision-7",
      useStyle: true,
      providerId: "grsai-main",
      model: "gpt-image-2",
      imageSize: "2K",
      allowUnconstrained: false,
    };
    expect(narrativeGroupActionPayload({
      revision: 8,
      aspectRatio: "16:9",
      selection: {
        ...plannedSelection,
        displayName: "must-not-leak",
        localPath: "/private/must-not-leak.png",
      },
    } as never)).toEqual({
      revision: 8,
      aspect_ratio: "16:9",
      reference_resolution: {
        selected_binding_ids: ["binding-2", "binding-1"],
        upload_ids: ["upload-1"],
        reference_revision: "plan-revision-7",
      },
      use_style: true,
      provider_id: "grsai-main",
      model: "gpt-image-2",
      image_size: "2K",
      allow_unconstrained: false,
    });
  });

  it("builds revision history and rollback endpoints with canonical stages", () => {
    expect(narrativeGroupRevisionPath("demo", 2, "ng-01", "sketch"))
      .toBe("api/v1/projects/demo/episodes/2/narrative-groups/ng-01/sketch/revisions");
    expect(narrativeGroupRollbackPath("demo", 2, "ng-01", "render", 4))
      .toBe("api/v1/projects/demo/episodes/2/narrative-groups/ng-01/render/revisions/4/rollback");
  });

  it("uses a stable cache key beneath the episode", () => {
    expect(queryKeys.narrativeGroups("demo", 2)).toEqual([
      "projects", "demo", "episodes", 2, "narrative-groups",
    ]);
  });
});

const server = setupServer();

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return createElement(QueryClientProvider, { client }, children);
}

describe("narrative group reference hooks", () => {
  it("surfaces the backend message when reference planning returns a FastAPI conflict", async () => {
    server.use(http.get(
      "http://localhost:3000/api/v1/projects/demo/episodes/2/narrative-groups/ng-1/render/references",
      () => HttpResponse.json({
        detail: {
          code: "REFERENCE_REVISION_CONFLICT",
          message: "引用预览已过期，请刷新后重试",
        },
      }, { status: 409 }),
    ));

    const { result } = renderHook(
      () => useNarrativeGroupReferences("demo", 2, "ng-1", "render", true),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(BackendStatusError);
    expect(result.current.error).toMatchObject({
      status: 409,
      message: "引用预览已过期，请刷新后重试",
    });
  });

  it("sends the complete revisioned request when retrying one segment", async () => {
    let body: unknown;
    server.use(http.post(
      "http://localhost:3000/api/v1/projects/demo/episodes/2/narrative-groups/ng-1/video/segments/seg-1/generate",
      async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ok: true, task_type: "video", scope: "segment", message: "started" });
      },
    ));
    const { result } = renderHook(() => useGenerateNarrativeGroupVideoSegment("demo", 2), { wrapper });
    await result.current.mutateAsync({groupId:"ng-1",segmentId:"seg-1",model:"runninghub:minimax-h3-ref",mode:"auto",revision:6,planRevision:3,settingsRevision:5,referenceRevision:7,aspectRatio:"16:9"});
    expect(body).toEqual({model:"runninghub:minimax-h3-ref",mode:"auto",revision:6,plan_revision:3,settings_revision:5,reference_revision:7,aspect_ratio:"16:9"});
  });
  it("sends a reference revision for the Ref model without changing legacy generation bodies", async () => {
    const bodies: unknown[] = [];
    server.use(http.post(
      "http://localhost:3000/api/v1/projects/demo/episodes/2/narrative-groups/ng-1/video/generate",
      async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ ok: true, task_type: "video", scope: "group_ng-1_video_r4", message: "started" });
      },
    ));
    const { result } = renderHook(() => useGenerateNarrativeGroupVideo("demo", 2), { wrapper });

    await result.current.mutateAsync({
      groupId: "ng-1",
      model: "runninghub:minimax-h3-ref",
      mode: "auto",
      revision: 4,
      planRevision: 7,
      referenceRevision: 3,
      aspectRatio: "9:16",
    });
    await result.current.mutateAsync({
      groupId: "ng-1",
      model: "runninghub:minimax-h3",
      mode: "auto",
      revision: 4,
      planRevision: 7,
      aspectRatio: "9:16",
    });

    expect(bodies[0]).toMatchObject({
      model: "runninghub:minimax-h3-ref",
      reference_revision: 3,
    });
    expect(bodies[1]).not.toHaveProperty("reference_revision");
  });

  it("uses the exact encoded video reference endpoints", () => {
    expect(narrativeGroupVideoReferencePreviewPath("demo project", 2, "组 一"))
      .toBe("api/v1/projects/demo%20project/episodes/2/narrative-groups/%E7%BB%84%20%E4%B8%80/video/reference-preview");
    expect(narrativeGroupVideoReferenceUploadPath("demo project", 2, "组 一"))
      .toBe("api/v1/projects/demo%20project/episodes/2/narrative-groups/%E7%BB%84%20%E4%B8%80/video/reference-uploads");
    expect(narrativeGroupVideoReferencesPath("demo project", 2, "组 一"))
      .toBe("api/v1/projects/demo%20project/episodes/2/narrative-groups/%E7%BB%84%20%E4%B8%80/video/references");
  });

  it("requests the video reference preview only according to caller enabled", async () => {
    const get = vi.spyOn((await import("@/lib/api")).api, "get");
    const disabled = renderHook(
      () => useNarrativeGroupVideoReferencePreview("demo", 2, "ng-1", false),
      { wrapper },
    );
    expect(disabled.result.current.fetchStatus).toBe("idle");
    expect(get).not.toHaveBeenCalled();
    disabled.unmount();

    const enabled = renderHook(
      () => useNarrativeGroupVideoReferencePreview("", 0, "", true),
      { wrapper },
    );
    await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
    enabled.unmount();
  });

  it("uploads a video reference as multipart and invalidates only its preview", async () => {
    let contentType = "";
    server.use(http.post(
      "http://localhost:3000/api/v1/projects/demo/episodes/2/narrative-groups/ng-1/video/reference-uploads",
      ({ request }) => {
        contentType = request.headers.get("content-type") ?? "";
        return HttpResponse.json({
          ok: true,
          data: {
            reference_id: "upload-1",
            source_kind: "temporary_upload",
            label: "ref.png",
            subject_description: "temporary uploaded reference image",
            thumbnail_url: "/api/v1/projects/demo/assets/ref.png",
          },
        });
      },
    ));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateQueries = vi.spyOn(client, "invalidateQueries");
    const uploadWrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client }, children);
    const post = vi.spyOn(api, "post");
    const { result } = renderHook(() => useUploadNarrativeGroupVideoReference("demo", 2), {
      wrapper: uploadWrapper,
    });
    const file = new File(["image"], "ref.png", { type: "image/png" });

    const response = await result.current.mutateAsync({ groupId: "ng-1", file });

    expect(contentType).toContain("multipart/form-data");
    expect(response).toMatchObject({
      ok: true,
      data: { reference_id: "upload-1", source_kind: "temporary_upload" },
    });
    expect(post).toHaveBeenCalledWith(
      "api/v1/projects/demo/episodes/2/narrative-groups/ng-1/video/reference-uploads",
      { body: expect.any(FormData) },
    );
    const body = post.mock.calls[0][1]?.body as FormData;
    expect(body.get("file")).toMatchObject({ name: "ref.png", type: "image/png", size: 5 });
    expect(invalidateQueries).toHaveBeenCalledTimes(1);
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: narrativeGroupVideoReferencePreviewQueryKey("demo", 2, "ng-1"), exact: true,
    });
  });

  it("puts ordered references with the expected revision and invalidates only scoped caches", async () => {
    let body: unknown;
    server.use(http.put(
      "http://localhost:3000/api/v1/projects/demo/episodes/2/narrative-groups/ng-1/video/references",
      async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ok: true, data: { revision: 4, max_images: 5, candidates: [], selected: [], warnings: [] } });
      },
    ));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateQueries = vi.spyOn(client, "invalidateQueries");
    const updateWrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client }, children);
    const { result } = renderHook(() => useUpdateNarrativeGroupVideoReferences("demo", 2), {
      wrapper: updateWrapper,
    });

    await result.current.mutateAsync({
      groupId: "ng-1",
      expectedRevision: 3,
      references: [
        { reference_id: "prop-2", subject_description: "brass key" },
        { reference_id: "char-1", subject_description: "Alice in blue" },
      ],
    });

    expect(body).toEqual({
      expected_revision: 3,
      references: [
        { reference_id: "prop-2", subject_description: "brass key" },
        { reference_id: "char-1", subject_description: "Alice in blue" },
      ],
    });
    expect(invalidateQueries).toHaveBeenNthCalledWith(1, {
      queryKey: narrativeGroupVideoReferencePreviewQueryKey("demo", 2, "ng-1"), exact: true,
    });
    expect(invalidateQueries).toHaveBeenNthCalledWith(2, {
      queryKey: queryKeys.narrativeGroups("demo", 2), exact: true,
    });
  });

  it("puts a manual video plan and returns the updated group", async () => {
    let body: unknown;
    server.use(http.put(
      "http://localhost:3000/api/v1/projects/demo/episodes/2/narrative-groups/ng-1/video/plan",
      async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ok: true, data: { id: "ng-1", video_plan: { revision: 5 } } });
      },
    ));

    const { result } = renderHook(() => useUpdateNarrativeGroupVideoPlan("demo", 2), { wrapper });
    const response = await result.current.mutateAsync({
      groupId: "ng-1",
      expectedRevision: 4,
      units: [{ beatIds: ["8", "9"] }, { beatIds: ["10"] }],
    });

    expect(body).toEqual({
      expected_revision: 4,
      units: [{ beat_ids: ["8", "9"] }, { beat_ids: ["10"] }],
    });
    expect(response).toMatchObject({ ok: true, data: { id: "ng-1", video_plan: { revision: 5 } } });
  });

  it("requests references only when enabled", async () => {
    let requests = 0;
    server.use(http.get(
      "http://localhost:3000/api/v1/projects/demo/episodes/2/narrative-groups/ng-1/render/references",
      () => {
        requests += 1;
        return HttpResponse.json({ ok: true, data: { warnings: [] } });
      },
    ));

    const disabled = renderHook(
      () => useNarrativeGroupReferences("demo", 2, "ng-1", "render", false),
      { wrapper },
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(disabled.result.current.fetchStatus).toBe("idle");
    expect(requests).toBe(0);
    disabled.unmount();

    const enabled = renderHook(
      () => useNarrativeGroupReferences("demo", 2, "ng-1", "render", true),
      { wrapper },
    );
    await waitFor(() => expect(enabled.result.current.data).toBeDefined());
    expect(requests).toBeGreaterThan(0);
  });

  it("surfaces the structured planning reason when reference preview returns 409", async () => {
    server.use(http.get(
      "http://localhost:3000/api/v1/projects/demo/episodes/2/narrative-groups/ng-1/render/references",
      () => HttpResponse.json({
        detail: {
          code: "PLANNED_REFERENCES_REQUIRED",
          message: "当前导演方案仍有未规划的必需引用: prop:深灰功德碑",
        },
      }, { status: 409 }),
    ));

    const query = renderHook(
      () => useNarrativeGroupReferences("demo", 2, "ng-1", "render", true),
      { wrapper },
    );

    await waitFor(() => expect(query.result.current.isError).toBe(true));
    expect(query.result.current.error).toMatchObject({
      message: "当前导演方案仍有未规划的必需引用: prop:深灰功德碑",
    });
  });

  it("uploads generation-only references as file-only form data", async () => {
    const fields: string[] = [];
    server.use(http.post(
      "http://localhost:3000/api/v1/projects/demo/episodes/2/narrative-groups/ng-1/render/references/upload",
      async ({ request }) => {
        const form = await request.formData();
        fields.push(...form.keys());
        return HttpResponse.json({ ok: true, data: { upload_id: "upload-1", temporary: true, persisted: false, url: "/upload-1" } });
      },
    ));
    const upload = renderHook(
      () => useUploadNarrativeReference("demo", 2, "ng-1", "render"),
      { wrapper },
    );

    await upload.result.current.mutateAsync({ file: new File(["image"], "reference.png", { type: "image/png" }) });

    expect(fields).toEqual(["file"]);
  });

  it("drops selection for split and sends it for generate", async () => {
    const bodies: unknown[] = [];
    server.use(http.post(
      "http://localhost:3000/api/v1/projects/demo/episodes/2/narrative-groups/ng-1/render/:action",
      async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ ok: true, task_type: "grid", scope: "grid", message: "started" });
      },
    ));
    const { result } = renderHook(() => useNarrativeGroupAction("demo", 2), { wrapper });
    const selection: PlannedNarrativeGroupGenerationSelection = {
      useStyle: true,
      selectedBindingIds: [],
      uploadIds: ["upload-1"],
      referenceRevision: "planned-r1",
    };

    await result.current.mutateAsync({ groupId: "ng-1", stage: "render", action: "split", selection, aspectRatio: "16:9" });
    await result.current.mutateAsync({ groupId: "ng-1", stage: "render", action: "generate", selection, aspectRatio: "16:9" });

    expect(bodies).toEqual([
      { aspect_ratio: "16:9" },
      {
        aspect_ratio: "16:9",
        use_style: true,
        reference_resolution: {
          selected_binding_ids: [],
          upload_ids: ["upload-1"],
          reference_revision: "planned-r1",
        },
      },
    ]);
  });
});
