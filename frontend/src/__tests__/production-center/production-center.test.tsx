// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  preview: vi.fn(),
  create: vi.fn(),
  list: vi.fn(),
  get: vi.fn(),
  retry: vi.fn(),
}));

vi.mock("@/api/media-production", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/media-production")>();
  return {
    ...actual,
    previewProductionRun: mocks.preview,
    createProductionRun: mocks.create,
    listProductionRuns: mocks.list,
    getProductionRun: mocks.get,
    retryProductionNode: mocks.retry,
  };
});

import { ProductionCenter } from "@/features/media-production/ProductionCenter";
import { useTaskCenterStore } from "@/task-center/store";

const preview = {
  audit_policy: "balanced" as const,
  nodes: [
    { node_id: "image-1", node_type: "image", action: "create" as const, fingerprint: "a", quantity: 1, estimated_cost: 0.5, reason: "missing_artifact", upstream_hashes: {} },
    { node_id: "voice-1", node_type: "tts", action: "reuse" as const, fingerprint: "b", quantity: 1, estimated_cost: 0, reason: "content_hash_match", upstream_hashes: {} },
    { node_id: "video-1", node_type: "video", action: "invalidate" as const, fingerprint: "c", quantity: 1, estimated_cost: 2, reason: "content_hash_changed", upstream_hashes: { "image-1": "a" } },
    { node_id: "metadata", node_type: "metadata", action: "skip" as const, fingerprint: "d", quantity: 1, estimated_cost: 0, reason: "scope_skip", upstream_hashes: {} },
  ],
  counts: { create: 1, reuse: 1, invalidate: 1, skip: 1 },
  estimated_cost: 2.5,
  snapshot_token: "confirmed-snapshot",
};

describe("ProductionCenter", () => {
  beforeEach(() => {
    mocks.preview.mockReset().mockResolvedValue(preview);
    mocks.create.mockReset().mockResolvedValue({
      run: { id: "run-1", project_id: "demo", status: "running", config_snapshot: {}, created_at: "2026-08-15T00:00:00Z", updated_at: "2026-08-15T00:00:00Z" },
      nodes: [],
      edges: [],
    });
    mocks.list.mockReset().mockResolvedValue({ items: [], page: 1, page_size: 20, total: 0, pages: 0 });
    mocks.get.mockReset();
    mocks.retry.mockReset();
    useTaskCenterStore.getState().reset();
    useTaskCenterStore.getState().setProject("demo");
  });

  it("shows preview actions, budget, missing assets and starts with the confirmed token", async () => {
    const user = userEvent.setup();
    render(<ProductionCenter project="demo" />);

    await user.click(screen.getByRole("button", { name: "生成预览" }));

    expect(await screen.findByText("缺失资产")).toBeInTheDocument();
    expect(screen.getByText("image-1")).toBeInTheDocument();
    expect(screen.getByText("2.50 / 10.00")).toBeInTheDocument();
    for (const action of ["创建", "复用", "失效重建", "跳过"]) {
      expect(screen.getByText(action)).toBeInTheDocument();
    }

    await user.click(screen.getByRole("button", { name: "确认并启动" }));

    expect(mocks.create).toHaveBeenCalledWith("demo", {
      draft: expect.any(Object),
      snapshotToken: "confirmed-snapshot",
    });
  });

  it("invalidates a preview confirmation when the budget changes", async () => {
    const user = userEvent.setup();
    render(<ProductionCenter project="demo" />);

    await user.click(screen.getByRole("button", { name: "生成预览" }));
    const start = await screen.findByRole("button", { name: "确认并启动" });
    expect(start).toBeEnabled();

    const budget = screen.getByRole("spinbutton", { name: "批次预算" });
    await user.clear(budget);
    await user.type(budget, "12");

    expect(start).toBeDisabled();
    expect(screen.getByText("配置已变化，请重新生成预览")).toBeInTheDocument();
  });

  it("reacts to production task updates from the existing task-center store", async () => {
    render(<ProductionCenter project="demo" />);
    expect(screen.getByText("当前无生产任务")).toBeInTheDocument();

    act(() => {
      useTaskCenterStore.getState().upsert({
        task_key: "production:run-1:video-1",
        task_id: "task-1",
        task_type: "production_node",
        username: "alice",
        project: "demo",
        project_id: "demo",
        episode: 1,
        beat_num: null,
        scope: "production:run-1",
        status: "running",
        progress: 42,
        current_task: "video-1",
        result: null,
        metadata: { production_run_id: "run-1", provider_id: "video-a", resource_capacity: 2 },
        error: null,
        logs: [],
        created_at: "2026-08-15T00:00:00Z",
        updated_at: "2026-08-15T00:01:00Z",
        completed_at: "",
      });
    });

    await waitFor(() => expect(screen.getByText("video-1")).toBeInTheDocument());
    expect(screen.getAllByText("42%")).toHaveLength(2);
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
  });

  it("exposes the VoiceProfile review entry", () => {
    render(<ProductionCenter project="demo" />);
    expect(screen.getByRole("link", { name: "审核 VoiceProfile" })).toHaveAttribute(
      "href",
      "/projects/demo/characters?tab=voices",
    );
  });
});
