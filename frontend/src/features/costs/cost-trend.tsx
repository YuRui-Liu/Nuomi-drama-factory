// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import type { CostTrendPoint, ProjectCostSnapshot } from '@/types/project-costs';
import { money } from './cost-format';

const series = [
  { key: 'total_cents', label: 'knownTotal', fallback: '已知按量费用', color: 'text-violet-600 dark:text-violet-400' },
  { key: 'confirmed_cents', label: 'confirmed', fallback: '已确认', color: 'text-teal-700 dark:text-teal-400' },
  { key: 'estimated_cents', label: 'estimated', fallback: '估算', color: 'text-amber-700 dark:text-amber-400' },
] as const;

export function CostTrend({ snapshot }: { snapshot: ProjectCostSnapshot }) {
  const { t } = useTranslation();
  const heading = useId();
  const [mode, setMode] = useState<'daily' | 'cumulative'>('daily');
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const points = snapshot.trend[mode];
  const selected = points.find(point => point.date === selectedDate) ?? points[points.length - 1];
  const amount = (value: number | null) => value === null ? t('costs.unpriced', '待核算') : money(value);
  const completeness = (point: CostTrendPoint) => point.complete ? t('costs.trendComplete', '完整') : t('costs.trendIncomplete', '不完整 · 已知金额小计');
  const description = (point: CostTrendPoint) => `${point.date} · ${series.map(item => `${t(`costs.${item.label}`, item.fallback)}：${amount(point[item.key])}`).join(' · ')} · ${t('costs.unpricedCalls', '待核算调用')}：${point.unpriced_count} · ${completeness(point)}`;
  const hasMoney = snapshot.summary.priced_count > 0 && points.some(point => series.some(item => point[item.key] !== null));
  const state = snapshot.summary.display_state;
  const emptyText = snapshot.summary.cny_attempt_count === 0 ? t('costs.noCnyTrend', '暂无人民币支出趋势；RH 币按套餐独立统计') : state === 'subscription_only' ? t('costs.trendSubscription', '订阅覆盖，暂无按量费用曲线') : state === 'unpriced_only' ? t('costs.trendUnpriced', '费用待核算，暂无可绘制金额') : t('costs.trendEmpty', '暂无费用记录');
  const width = Math.max(680, 120 + (points.length - 1) * 48);
  const height = 260;
  const max = Math.max(1, ...points.flatMap(point => series.map(item => point[item.key] ?? 0)));
  const x = (index: number) => points.length === 1 ? (width + 48) / 2 : 88 + index * (width - 120) / (points.length - 1);
  const y = (value: number) => 208 - value / max * 176;
  const tickStep = Math.max(1, Math.ceil(points.length / 7));
  return <Card aria-labelledby={heading}>
    <CardContent className="space-y-4 pt-6">
      <div className="flex flex-wrap items-center justify-between gap-3"><h2 id={heading} className="font-semibold">{t('costs.trendTitle', '支出趋势')}</h2><div className="flex gap-2">
        {(['daily', 'cumulative'] as const).map(value => <Button key={value} size="sm" variant={mode === value ? 'default' : 'outline'} aria-pressed={mode === value} onClick={() => setMode(value)}>{value === 'daily' ? t('costs.trendDaily', '每日支出') : t('costs.trendCumulative', '累计支出')}</Button>)}
      </div></div>
      <p className="text-xs text-muted-foreground">{t('costs.trendHint', '项目全周期 · 人民币 CNY · 未知金额处断开；不完整日期仅展示已知金额。可聚焦或点选日期查看明细。')}</p>
      {hasMoney ? <>
        <div className="flex flex-wrap gap-5 text-xs">{series.map(item => <span key={item.key} className={`inline-flex items-center gap-2 ${item.color}`}><svg aria-hidden="true" width="24" height="8"><line x1="0" y1="4" x2="24" y2="4" stroke="currentColor" strokeWidth="3" strokeDasharray={item.key === 'estimated_cents' ? '5 3' : undefined} /></svg>{t(`costs.${item.label}`, item.fallback)}</span>)}</div>
        <div data-chart-scroll className="overflow-x-auto pb-2">
          <svg role="group" aria-label={t('costs.trendTitle', '支出趋势')} viewBox={`0 0 ${width} ${height}`} className="w-full" style={{ minWidth: width }}>
            {[0, 0.5, 1].map(ratio => <g key={ratio} className="text-muted-foreground" aria-hidden="true"><line x1="88" x2={width - 32} y1={y(max * ratio)} y2={y(max * ratio)} stroke="currentColor" opacity="0.15" /><text x="78" y={y(max * ratio) + 4} textAnchor="end" fill="currentColor" fontSize="11">{money(max * ratio)}</text></g>)}
            {series.map(item => {
              const segments: string[][] = [];
              let current: string[] = [];
              points.forEach((point, index) => {
                const value = point[item.key];
                if (value === null) { current = []; return; }
                if (!current.length) segments.push(current);
                current.push(`${x(index)},${y(value)}`);
              });
              return <g key={item.key} className={item.color} aria-hidden="true">
                {segments.map((segment, index) => <polyline key={index} data-series={item.key} points={segment.join(' ')} fill="none" stroke="currentColor" strokeWidth={item.key === 'total_cents' ? 3 : 2} strokeDasharray={item.key === 'estimated_cents' ? '6 4' : undefined} />)}
                {points.map((point, index) => point[item.key] !== null && <circle key={point.date} cx={x(index)} cy={y(point[item.key]!)} r="3" fill="currentColor" />)}
              </g>;
            })}
            {points.map((point, index) => <g key={point.date} role="button" tabIndex={0} aria-label={description(point)} onFocus={() => setSelectedDate(point.date)} onMouseEnter={() => setSelectedDate(point.date)} onClick={() => setSelectedDate(point.date)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelectedDate(point.date); } }} className="group cursor-pointer outline-none">
              <rect x={x(index) - 20} y="20" width="40" height="196" rx="5" fill="currentColor" className="text-primary opacity-0 group-focus:opacity-10 group-hover:opacity-10" />
              {selected?.date === point.date && <line aria-hidden="true" x1={x(index)} x2={x(index)} y1="20" y2="216" stroke="currentColor" className="text-muted-foreground" strokeDasharray="3 4" opacity="0.4" />}
              {(index % tickStep === 0 || index === points.length - 1) && <text aria-hidden="true" x={x(index)} y="240" textAnchor="middle" fontSize="11" fill="currentColor" className="text-muted-foreground">{point.date.slice(5)}</text>}
            </g>)}
          </svg>
        </div>
        <p role="status" aria-live="polite" aria-atomic="true" className="rounded-lg bg-muted/50 p-3 text-sm leading-6 tabular-nums">{selected && description(selected)}</p>
      </> : <p className="py-12 text-center text-sm text-muted-foreground">{emptyText}</p>}
    </CardContent>
  </Card>;
}
