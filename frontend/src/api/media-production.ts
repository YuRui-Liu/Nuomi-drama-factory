// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { apiCall } from "@/api/client";

export type ProductionAuditPolicy = "strict" | "balanced" | "auto";
export type ProductionPlanAction = "create" | "reuse" | "invalidate" | "skip";
export type ProductionRunStatus =
  | "running"
  | "paused"
  | "cancelling"
  | "succeeded"
  | "failed"
  | "cancelled";
export type ProductionNodeStatus =
  | "pending"
  | "ready"
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "quality_failed"
  | "cancelled"
  | "skipped";

export interface ProductionNodeTypeSpec {
  capability: string;
  implementation: string;
  workflow_version: unknown;
  unit_cost?: number;
}

export interface ProductionScopeNode {
  id: string;
  node_type: string;
  depends_on?: string[];
  inputs?: Record<string, unknown>;
  params?: Record<string, unknown>;
  quantity?: number;
  skip?: boolean;
  [key: string]: unknown;
}

export interface ProductionRunDraft {
  node_types: Record<string, ProductionNodeTypeSpec>;
  scope: {
    audit_policy?: ProductionAuditPolicy;
    nodes: ProductionScopeNode[];
    edges?: Array<Record<string, unknown> | [string, string]>;
    [key: string]: unknown;
  };
  snapshot: Record<string, unknown>;
  existing_artifacts: Record<string, unknown> | Array<Record<string, unknown>>;
  high_cost: boolean;
}

export interface ProductionPlanNode {
  node_id: string;
  node_type: string;
  action: ProductionPlanAction;
  fingerprint: string;
  quantity: number;
  estimated_cost: number;
  reason: string | null;
  upstream_hashes: Record<string, string>;
}

export interface ProductionPlanPreview {
  audit_policy: ProductionAuditPolicy;
  nodes: ProductionPlanNode[];
  counts: Record<ProductionPlanAction, number>;
  estimated_cost: number;
  snapshot_token: string;
}

export interface ProductionRun {
  id: string;
  project_id: string;
  status: ProductionRunStatus;
  config_snapshot: unknown;
  created_at: string;
  updated_at: string;
}

export interface ProductionNode {
  id: string;
  run_id: string;
  node_type: string;
  idempotency_key: string;
  status: ProductionNodeStatus;
  config_snapshot: unknown;
  created_at: string;
  updated_at: string;
}

export interface ProductionEdge {
  id: string;
  run_id: string;
  upstream_node_id: string;
  downstream_node_id: string;
  created_at: string;
}

export interface ProductionRunDetail {
  run: ProductionRun;
  nodes: ProductionNode[];
  edges: ProductionEdge[];
}

export interface ProductionRunPage {
  items: ProductionRun[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
}

function runsPath(project: string): string {
  return `projects/${encodeURIComponent(project)}/production/runs`;
}

export function previewProductionRun(
  project: string,
  draft: ProductionRunDraft,
): Promise<ProductionPlanPreview> {
  return apiCall(`${runsPath(project)}/preview`, { method: "post", json: draft });
}

export function createProductionRun(
  project: string,
  confirmation: { draft: ProductionRunDraft; snapshotToken: string },
): Promise<ProductionRunDetail> {
  const snapshotToken = confirmation.snapshotToken.trim();
  if (!snapshotToken) {
    return Promise.reject(new Error("A preview snapshot token is required to start production"));
  }
  return apiCall(runsPath(project), {
    method: "post",
    json: { ...confirmation.draft, snapshot_token: snapshotToken },
  });
}

export function listProductionRuns(
  project: string,
  page = 1,
  pageSize = 20,
): Promise<ProductionRunPage> {
  return apiCall(runsPath(project), {
    searchParams: { page: String(page), page_size: String(pageSize) },
  });
}

export function getProductionRun(project: string, runId: string): Promise<ProductionRunDetail> {
  return apiCall(`${runsPath(project)}/${encodeURIComponent(runId)}`);
}

export function pauseProductionRun(project: string, runId: string): Promise<ProductionRun> {
  return apiCall(`${runsPath(project)}/${encodeURIComponent(runId)}/pause`, { method: "post" });
}

export function resumeProductionRun(project: string, runId: string): Promise<ProductionRun> {
  return apiCall(`${runsPath(project)}/${encodeURIComponent(runId)}/resume`, { method: "post" });
}

export function cancelProductionRun(project: string, runId: string): Promise<ProductionRun> {
  return apiCall(`${runsPath(project)}/${encodeURIComponent(runId)}/cancel`, { method: "post" });
}

export function retryProductionNode(
  project: string,
  runId: string,
  nodeId: string,
): Promise<ProductionNode> {
  return apiCall(`${runsPath(project)}/${encodeURIComponent(runId)}/retry-node`, {
    method: "post",
    json: { node_id: nodeId },
  });
}
