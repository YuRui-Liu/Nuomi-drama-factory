// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useTranslation } from 'react-i18next';
import { Card, CardContent } from '@/components/ui/card';
import type { ProjectCostSnapshot } from '@/types/project-costs';
import { bucketAmount, costDate, money } from './cost-format';
export function CostSummary({ snapshot }: { snapshot: ProjectCostSnapshot }) {
  const { t } = useTranslation();
  const s = snapshot.summary;
  const reasons: Record<string, string> = { project_creation_unknown: '项目创建时间未知', coverage_missing: '部分渠道尚未建立监测记录', coverage_incomplete: '历史记录不完整', history_before_monitoring: '监测开始前的历史费用未纳入', coverage_gaps: '监测存在缺口', unpriced_attempts: '部分调用尚未定价', pending_submissions: '部分调用仍待提交确认' };
  return <div className="space-y-4">
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Card className="border-primary/20 bg-primary/5"><CardContent className="space-y-3 pt-6"><p className="text-sm text-muted-foreground">{t('costs.knownTotal', '已知按量费用')}</p><p data-testid="cost-total" className="text-3xl font-semibold tabular-nums tracking-tight">{bucketAmount(s, t('costs.unpriced', '待核算'), t('costs.subscriptionOnly', '暂无按量费用'))}</p><p className="text-xs text-muted-foreground">{t('costs.totalHint', '已确认 + 估算 · 不含共享订阅')}</p></CardContent></Card>
      {([['confirmed', s.confirmed_cents], ['estimated', s.estimated_cents]] as const).map(([kind, amount]) => <Card key={kind}><CardContent className="space-y-3 pt-6"><p className="text-sm text-muted-foreground">{t(`costs.${kind}`, kind === 'confirmed' ? '已确认' : '估算')}</p><p className="text-2xl font-semibold tabular-nums">{s.priced_count ? money(amount) : '—'}</p><p className="text-xs text-muted-foreground">{t(`costs.${kind}Hint`, kind === 'confirmed' ? '依据实际账单或计费回执' : '依据用量和匹配的价格规则')}</p></CardContent></Card>)}
      <Card><CardContent className="space-y-3 pt-6"><p className="text-sm text-muted-foreground">{t('costs.unpricedCalls', '待核算调用')}</p><p className="text-2xl font-semibold tabular-nums text-amber-600">{s.unpriced_count}</p><p className="text-xs text-muted-foreground">{t('costs.pendingCount', '另有 {{count}} 次待提交确认', { count: s.pending_count })}</p></CardContent></Card>
    </div>
    {!snapshot.coverage.complete && <div className="rounded-xl border border-amber-400/30 bg-amber-400/5 p-4 text-sm text-amber-800 dark:text-amber-200"><p className="font-medium">{t('costs.incomplete', '当前为已知费用小计，尚非完整项目成本')}</p><p className="mt-1">{snapshot.coverage.reasons.map(reason => t(`costs.reasons.${reason}`, reasons[reason] || '部分历史费用尚未纳入')).join(' · ')}</p><p className="mt-2 text-xs">{t('costs.monitoringStart', '监测起点')}：{costDate(snapshot.coverage.start_at)}</p>{snapshot.coverage.gaps.length > 0 && <p className="mt-1 text-xs">{t('costs.gaps', '监测缺口')}：{snapshot.coverage.gaps.join(' · ')}</p>}</div>}
  </div>;
}
