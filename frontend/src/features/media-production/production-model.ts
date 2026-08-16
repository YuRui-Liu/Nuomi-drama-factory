// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { ProductionPlanNode, ProductionPlanPreview } from "@/api/media-production";
import type { TaskState } from "@/task-center/types";

const ACTIVE_STATUSES = new Set(["submitting", "queued", "pending", "starting", "running"]);

function metadata(task: TaskState): Record<string, unknown> {
  return task.metadata && typeof task.metadata === "object" ? task.metadata : {};
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function positiveNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

export function productionRunId(task: TaskState): string | null {
  const meta = metadata(task);
  return (
    text(meta.production_run_id) ??
    text(meta.productionRunId) ??
    text(meta.run_id) ??
    text(meta.runId) ??
    (/^production:([^:]+)/.exec(task.scope ?? "")?.[1] ?? null)
  );
}

export function isProductionTask(task: TaskState): boolean {
  return (
    task.task_type.startsWith("production") ||
    task.task_key.startsWith("production:") ||
    (task.scope ?? "").startsWith("production:") ||
    productionRunId(task) !== null
  );
}

export function deriveMissingAssets(preview: ProductionPlanPreview | null): ProductionPlanNode[] {
  return preview?.nodes.filter(
    (node) => node.action === "create" && node.reason === "missing_artifact",
  ) ?? [];
}

export interface ResourcePoolSummary {
  providerId: string;
  active: number;
  capacity: number;
  occupancy: number;
}

export function deriveResourcePools(tasks: TaskState[]): ResourcePoolSummary[] {
  const pools = new Map<string, { active: number; capacity: number }>();
  for (const task of tasks) {
    if (!isProductionTask(task) || !ACTIVE_STATUSES.has(task.status)) continue;
    const meta = metadata(task);
    const providerId = text(meta.provider_id) ?? text(meta.providerId) ?? "local";
    const capacity =
      positiveNumber(meta.resource_capacity) ?? positiveNumber(meta.capacity) ?? 1;
    const current = pools.get(providerId) ?? { active: 0, capacity };
    current.active += 1;
    current.capacity = Math.max(current.capacity, capacity, current.active);
    pools.set(providerId, current);
  }
  return [...pools.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([providerId, pool]) => ({
      providerId,
      active: pool.active,
      capacity: pool.capacity,
      occupancy: Math.min(1, pool.active / pool.capacity),
    }));
}

export interface DagProgress {
  total: number;
  completed: number;
  failed: number;
  running: number;
  progress: number;
}

export function deriveDagProgress(tasks: TaskState[], runId: string | null): DagProgress {
  const scoped = tasks.filter(
    (task) => isProductionTask(task) && (!runId || productionRunId(task) === runId),
  );
  const completed = scoped.filter((task) => task.status === "completed").length;
  const failed = scoped.filter(
    (task) => task.status === "failed" || task.status === "cancelled",
  ).length;
  const running = scoped.filter((task) => ACTIVE_STATUSES.has(task.status)).length;
  const progress = scoped.length
    ? Math.round(
        scoped.reduce((sum, task) => {
          if (task.status === "completed") return sum + 100;
          return sum + Math.max(0, Math.min(100, task.progress || 0));
        }, 0) / scoped.length,
      )
    : 0;
  return { total: scoped.length, completed, failed, running, progress };
}
