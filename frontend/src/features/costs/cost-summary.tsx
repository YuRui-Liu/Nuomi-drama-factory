// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useTranslation } from 'react-i18next';
import { Card, CardContent } from '@/components/ui/card';
import type { ProjectCostSnapshot } from '@/types/project-costs';
import { bucketAmount, money } from './cost-format';
export function CostSummary({ snapshot }: { snapshot: ProjectCostSnapshot }) {
  const { t } = useTranslation();
  const s = snapshot.summary;
  return <div className="space-y-4">
    {!!snapshot.historical_credits?.task_count && <div className="rounded-xl border border-primary/30 bg-primary/5 p-4"><p className="text-sm text-muted-foreground">{t('costs.historicalUsage', 'RunningHub 套餐消费（已确认）')}</p><p className="mt-2 text-3xl font-semibold tabular-nums">{snapshot.historical_credits.credit} RH 币</p><p className="mt-2 text-xs text-muted-foreground">{snapshot.historical_credits.task_count} {t('costs.historicalReceipts', '个任务的实际扣费，按 RH 币独立计费，不折算人民币。历史日期未知，不计入日期趋势。')}</p></div>}
    {snapshot.measured_credits?.map(value => <div key={`${value.provider}:${value.account_id}`} className="rounded-xl border bg-primary/5 p-4"><p className="text-sm text-muted-foreground">{value.provider === 'runninghub' ? 'RunningHub 实际消费' : `${value.provider} 积分消费`} · {value.account_id}</p><p className="mt-2 text-2xl font-semibold tabular-nums">{value.credit} {value.provider === 'runninghub' ? 'RH 币' : '积分'}</p><p className="mt-1 text-xs text-muted-foreground">{t('costs.nativeCredits', '平台实际扣费，按 RH 币独立计费，不折算人民币。')}</p></div>)}
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Card className="border-primary/20 bg-primary/5"><CardContent className="space-y-3 pt-6"><p className="text-sm text-muted-foreground">{t('costs.cnyTotal', '人民币费用')}</p><p data-testid="cost-total" className="text-3xl font-semibold tabular-nums tracking-tight">{s.cny_attempt_count === 0 ? t('costs.noCnyCharges', '暂无人民币费用记录') : bucketAmount(s, t('costs.unpriced', '待核算'), t('costs.subscriptionOnly', '暂无按量费用'))}</p><p className="text-xs text-muted-foreground">{t('costs.cnyTotalHint', '仅人民币计费 · 不含 RH 币和共享订阅')}</p></CardContent></Card>
      {([['confirmed', s.confirmed_cents], ['estimated', s.estimated_cents]] as const).map(([kind, amount]) => <Card key={kind}><CardContent className="space-y-3 pt-6"><p className="text-sm text-muted-foreground">{t(`costs.${kind}`, kind === 'confirmed' ? '已确认' : '估算')}</p><p className="text-2xl font-semibold tabular-nums">{s.priced_count ? money(amount) : '—'}</p><p className="text-xs text-muted-foreground">{t(`costs.${kind}Hint`, kind === 'confirmed' ? '依据实际账单或计费回执' : '依据用量和匹配的价格规则')}</p></CardContent></Card>)}
      <Card><CardContent className="space-y-3 pt-6"><p className="text-sm text-muted-foreground">{t('costs.unknownBillingCalls', '扣费金额未知的调用')}</p><p className="text-2xl font-semibold tabular-nums text-amber-600">{s.unpriced_count}</p><p className="text-xs text-muted-foreground">{t('costs.pendingCount', '另有 {{count}} 次待提交确认', { count: s.pending_count })}</p></CardContent></Card>
    </div>
    {!snapshot.coverage.complete && <p className="rounded-lg bg-muted/40 px-4 py-3 text-sm text-muted-foreground">{t('costs.partialHistorySimple', '部分历史扣费记录缺失，当前展示已找回的消费。RH 币按套餐独立统计。')}</p>}
  </div>;
}
