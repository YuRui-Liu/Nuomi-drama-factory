// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export type CostMedia = 'image' | 'audio' | 'video' | 'text';
export type CostStatus = 'confirmed' | 'estimated' | 'unpriced' | 'subscription_covered';
export interface CostBucket {
  total_cents: number; confirmed_cents: number; estimated_cents: number;
  priced_count: number; unpriced_count: number; subscription_count: number;
  pending_count: number; attempt_count: number;
}
export interface CostTrendPoint {
  date: string; total_cents: number | null; confirmed_cents: number | null;
  estimated_cents: number | null; unpriced_count: number; complete: boolean;
}
export interface CostSubscription {
  id: string; provider: string; account_id: string; starts_at: string; ends_at: string | null;
  amount_cents: number | null; currency: string; original_amount: string | null;
  cny_rate: string | null; source: string | null; project_call_count: number;
}
export interface ProjectCostSnapshot {
  project_id: string; currency: 'CNY'; timezone: string; snapshot_at: string;
  range: { from: string; to: string };
  summary: CostBucket & { complete: boolean; display_state: 'priced' | 'unpriced_only' | 'subscription_only' | 'empty' };
  trend: { daily: CostTrendPoint[]; cumulative: CostTrendPoint[] };
  breakdown: { channels: (CostBucket & { provider: string; media: (CostBucket & { media_type: CostMedia })[] })[]; media: (CostBucket & { media_type: CostMedia })[] };
  subscriptions: CostSubscription[];
  coverage: { start_at: string | null; complete: boolean; reasons: string[]; gaps: string[] };
}
export interface CostValue { status: CostStatus; amount_micros: number | null; reason: string | null }
export interface CostAttempt {
  attempt_id: string; project_id: string; provider: string; account_id: string; model: string;
  media_type: CostMedia; occurred_at: string; task_id: string | null; resource_id: string | null;
  external_id: string | null; execution_status: string; submission_status: string;
  usage: Record<string, string>; usage_source: string | null; workflow: string | null;
  specifications: [string, string][];
}
export interface CostEntry extends CostAttempt { cost_status: CostStatus; amount_cents: number | null; value: CostValue }
export interface CostRevision {
  value: CostValue; event_id?: string; sequence?: number; recorded_at?: string | null; applied?: boolean;
  usage?: Record<string, string>; usage_source?: string | null;
  evidence?: Record<string, string | number | null>;
  rule_snapshot?: { id: string; version: string; currency: string; cny_rate: string | null; items: { unit: string; unit_price: string; basis: string; step: string; minimum: string }[] } | null;
}
export interface CostEntryDetail { attempt: CostAttempt; current_cost: CostRevision & { amount_cents: number | null }; value: CostValue; revisions: CostRevision[] }
export interface CostFilters { channel?: string; media?: CostMedia; status?: CostStatus }
export interface CostEntriesResponse { entries: CostEntry[]; next_cursor: string | null }
