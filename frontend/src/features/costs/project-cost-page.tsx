// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { useProjectCostSnapshot } from '@/lib/queries/project-costs';
import type { CostFilters } from '@/types/project-costs';
import { CostSummary } from './cost-summary';
import { CostTrend } from './cost-trend';
import { CostBreakdown } from './cost-breakdown';
import { CostSubscriptions } from './cost-subscriptions';
import { CostEntries } from './cost-entries';
import { costDate } from './cost-format';
import { CostSettingsActions } from './cost-settings';

export function ProjectCostPage({ project }: { project: string }) { return <ProjectCostContent key={project} project={project} />; }
function ProjectCostContent({ project }: { project: string }) {
  const { t } = useTranslation();
  const query = useProjectCostSnapshot(project);
  const [filters, setFilters] = useState<CostFilters>({});
  const snapshot = query.data;
  return <div className="mx-auto w-full max-w-7xl space-y-6 pb-8"><header className="flex flex-wrap items-start justify-between gap-4"><div><h1 className="text-2xl font-semibold tracking-tight">{t('costs.title', '项目费用')}</h1><p className="mt-2 text-sm text-muted-foreground">{t('costs.scope', '项目全周期 · 人民币 CNY · 北京时间')}</p>{snapshot && <p className="mt-1 text-xs text-muted-foreground">{costDate(snapshot.range.from)} — {costDate(snapshot.range.to)}</p>}</div><div className="flex flex-col items-end gap-2"><Button variant="outline" disabled={query.isFetching} onClick={() => query.refresh()}>{t('costs.refresh', '刷新')}</Button>{snapshot && <p className="text-xs text-muted-foreground">{t('costs.updated', '更新于')} {costDate(snapshot.snapshot_at)}</p>}</div></header>
    {query.isError && <div role="alert" className="rounded-lg border border-destructive/30 p-4 text-sm text-destructive">{snapshot ? t('costs.staleError', '刷新失败，当前显示上次成功获取的数据。') : t('costs.loadError', '费用加载失败')} <Button variant="outline" onClick={() => query.refresh()}>{t('costs.retry', '重试')}</Button></div>}
    <CostSettingsActions project={project} />
    {query.isLoading && <p className="py-16 text-center text-muted-foreground">{t('costs.loading', '正在加载费用…')}</p>}
    {snapshot && <><CostSummary snapshot={snapshot} /><CostTrend snapshot={snapshot} /><CostBreakdown snapshot={snapshot} onFilter={setFilters} /><CostSubscriptions subscriptions={snapshot.subscriptions} /><CostEntries key={JSON.stringify(filters)} project={project} filters={filters} onFilter={setFilters} channels={snapshot.breakdown.channels.map(channel => channel.provider)} /></>}
  </div>;
}
