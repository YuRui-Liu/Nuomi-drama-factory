// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useTranslation } from 'react-i18next';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import type { CostSubscription } from '@/types/project-costs';
import { costDate, money } from './cost-format';
export function CostSubscriptions({ subscriptions }: { subscriptions: CostSubscription[] }) {
  const { t } = useTranslation();
  return <Card><CardHeader><CardTitle>{t('costs.subscriptions', '共享订阅')}</CardTitle><p className="text-sm text-muted-foreground">{t('costs.subscriptionHint', '账号级固定费用，可能由多个项目共享；不分摊、不计入本项目按量总额。')}</p></CardHeader><CardContent>{subscriptions.length ? <div className="grid gap-4 lg:grid-cols-2">{subscriptions.map(sub => <article key={sub.id} className="rounded-lg border p-4"><div className="flex justify-between gap-3"><h3 className="font-medium">{sub.provider} <span className="text-sm text-muted-foreground">/ {sub.account_id}</span></h3><span className="font-semibold tabular-nums">{sub.amount_cents === null ? t('costs.unpriced', '待核算') : money(sub.amount_cents)}</span></div><p className="mt-3 text-xs text-muted-foreground">{costDate(sub.starts_at)} — {sub.ends_at ? costDate(sub.ends_at) : t('costs.ongoing', '持续有效')}</p><p className="mt-2 text-sm">{t('costs.originalAmount', '原币金额')}：{sub.original_amount ?? '—'} {sub.currency} · {t('costs.rate', '人民币汇率')}：{sub.cny_rate ?? '—'}</p><p className="mt-2 text-sm">{t('costs.projectCalls', '本项目在该周期调用 {{count}} 次', { count: sub.project_call_count })}</p>{sub.source && <p className="mt-2 break-words text-xs text-muted-foreground">{t('costs.source', '来源')}：{sub.source}</p>}</article>)}</div> : <p className="py-3 text-sm text-muted-foreground">{t('costs.noSubscriptions', '暂无关联订阅')}</p>}</CardContent></Card>;
}
