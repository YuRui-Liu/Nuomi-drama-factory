// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { CostBucket } from '@/types/project-costs';
export const costMedia = ['image', 'audio', 'video', 'text'] as const;
export const mediaLabels = { image: '图片', audio: '音频', video: '视频', text: '文本' };
export const statusLabels = { confirmed: '已确认', estimated: '估算', unpriced: '待核算', subscription_covered: '订阅覆盖' };
export const money = (cents: number) => `¥${(cents / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const costDate = (value: string | null | undefined) => value ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) : '—';
export function bucketAmount(bucket: CostBucket | undefined, unknown: string, subscription: string): string {
  if (!bucket || !bucket.attempt_count) return '—';
  if (bucket.priced_count > 0) return money(bucket.total_cents);
  if (bucket.unpriced_count || bucket.pending_count) return unknown;
  return bucket.subscription_count ? subscription : '—';
}
