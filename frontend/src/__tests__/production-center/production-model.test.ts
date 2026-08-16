// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";
import {
  deriveDagProgress,
  deriveMissingAssets,
  deriveResourcePools,
  isProductionTask,
} from "@/features/media-production/production-model";
import type { ProductionPlanPreview } from "@/api/media-production";
import type { TaskState } from "@/task-center/types";

function task(overrides: Partial<TaskState>): TaskState {
  return {
    task_key: "production:run-1:node-1",
    task_id: "task-1",
    task_type: "production_node",
    username: "alice",
    project: "demo",
    project_id: "demo",
    episode: 1,
    beat_num: null,
    scope: "production:run-1",
    status: "running",
    progress: 35,
    current_task: "rendering",
    result: null,
    metadata: { production_run_id: "run-1", provider_id: "video-a" },
    error: null,
    logs: [],
    created_at: "2026-08-15T00:00:00Z",
    updated_at: "2026-08-15T00:01:00Z",
    completed_at: "",
    ...overrides,
  };
}

describe("production center domain model", () => {
  it("extracts missing assets while preserving all four preview actions", () => {
    const preview: ProductionPlanPreview = {
      audit_policy: "balanced",
      nodes: [
        { node_id: "image", node_type: "image", action: "create", fingerprint: "a", quantity: 1, estimated_cost: 1, reason: "missing_artifact", upstream_hashes: {} },
        { node_id: "voice", node_type: "tts", action: "reuse", fingerprint: "b", quantity: 1, estimated_cost: 0, reason: "content_hash_match", upstream_hashes: {} },
        { node_id: "video", node_type: "video", action: "invalidate", fingerprint: "c", quantity: 1, estimated_cost: 2, reason: "content_hash_changed", upstream_hashes: { image: "a" } },
        { node_id: "metadata", node_type: "metadata", action: "skip", fingerprint: "d", quantity: 1, estimated_cost: 0, reason: "scope_skip", upstream_hashes: {} },
      ],
      counts: { create: 1, reuse: 1, invalidate: 1, skip: 1 },
      estimated_cost: 3,
      snapshot_token: "token",
    };

    expect(deriveMissingAssets(preview)).toEqual([preview.nodes[0]]);
    expect(new Set(preview.nodes.map((node) => node.action))).toEqual(
      new Set(["create", "reuse", "invalidate", "skip"]),
    );
  });

  it("recognizes production tasks and summarizes provider occupancy", () => {
    const production = task({ metadata: { production_run_id: "run-1", provider_id: "video-a", resource_capacity: 3 } });
    const queued = task({ task_key: "production:run-1:node-2", status: "queued", metadata: { production_run_id: "run-1", provider_id: "video-a", resource_capacity: 3 } });
    const unrelated = task({ task_key: "chat:1", task_type: "chat", scope: "chat", metadata: {} });

    expect(isProductionTask(production)).toBe(true);
    expect(isProductionTask(unrelated)).toBe(false);
    expect(deriveResourcePools([production, queued, unrelated])).toEqual([
      { providerId: "video-a", active: 2, capacity: 3, occupancy: 2 / 3 },
    ]);
  });

  it("derives DAG progress from live task-center records", () => {
    const tasks = [
      task({ status: "completed", progress: 100, metadata: { production_run_id: "run-1" } }),
      task({ task_key: "production:run-1:node-2", status: "running", progress: 50, metadata: { production_run_id: "run-1" } }),
      task({ task_key: "production:run-2:node-1", metadata: { production_run_id: "run-2" } }),
    ];

    expect(deriveDagProgress(tasks, "run-1")).toEqual({
      total: 2,
      completed: 1,
      failed: 0,
      running: 1,
      progress: 75,
    });
  });
});
