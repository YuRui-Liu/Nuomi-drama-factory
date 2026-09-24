// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { CostBucket } from '@/types/project-costs';
import type { TFunction } from 'i18next';
const factLabels: Record<string, Record<string, string>> = {
  submission: { pending: '待提交确认', submitted: '已提交', failed: '提交失败', unknown: '提交状态未知' },
  execution: { pending: '等待执行', running: '执行中', succeeded: '执行成功', failed: '执行失败', cancelled: '已取消', unknown: '执行状态未知' },
  source: { request: '请求参数', provider: '服务商回执', response: '响应结果', measured: '实际测量', local: '本地记录', estimate: '估算用量', estimated: '估算用量', billing: '计费账单', manual: '手动录入' },
  unit: { item: '项', second: '秒', call: '次调用', character: '字符', input_tokens: '输入词元', output_tokens: '输出词元', credit: '积分' },
};
export function costFactLabel(t: TFunction, group: string, value: string | null | undefined): string {
  if (!value) return '—';
  return t(`costs.facts.${group}.${value}`, factLabels[group]?.[value] ?? value);
}
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
