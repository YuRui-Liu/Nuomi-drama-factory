import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, expect, it } from 'vitest';
import i18next from 'i18next';
import { initReactI18next } from 'react-i18next';
import type { ProjectCostSnapshot, CostTrendPoint } from '@/types/project-costs';
import { CostTrend } from './cost-trend';
beforeAll(async () => { await i18next.use(initReactI18next).init({ lng: 'zh', resources: {} }); });
afterEach(cleanup);
const point = (day: number, total: number | null): CostTrendPoint => ({ date: `2026-09-${20 + day}`, total_cents: total, confirmed_cents: total === null ? null : total - 1200, estimated_cents: total === null ? null : 1200, unpriced_count: day === 3 ? 2 : 0, complete: day !== 3 });
function snapshot(): ProjectCostSnapshot { return { summary: { display_state: 'priced', priced_count: 3 }, trend: { daily: [point(1, 4800), point(2, 9600), point(3, 13200)], cumulative: [point(1, 4800), point(2, 14400), point(3, 27600)] } } as ProjectCostSnapshot; }
it('switches between server daily and cumulative values and announces incomplete totals', () => {
  const data = snapshot(); data.trend.cumulative[1].total_cents = 14900;
  render(<CostTrend snapshot={data} />);
  expect(screen.getByRole('status')).toHaveTextContent('¥132.00');
  fireEvent.click(screen.getByRole('button', { name: '累计支出' }));
  expect(screen.getByRole('button', { name: '累计支出' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('status')).toHaveTextContent('¥276.00');
  expect(screen.getByRole('status')).toHaveTextContent('待核算调用：2');
  expect(screen.getByRole('status')).toHaveTextContent('不完整');
  fireEvent.focus(screen.getByRole('button', { name: /2026-09-22.*¥149.00/ }));
  expect(screen.getByRole('status')).toHaveTextContent('¥149.00');
});
it('updates date and amounts on keyboard focus and touch click', () => {
  render(<CostTrend snapshot={snapshot()} />);
  const first = screen.getByRole('button', { name: /2026-09-21.*¥48.00/ });
  expect(first).toHaveAttribute('tabindex', '0');
  fireEvent.focus(first);
  expect(screen.getByRole('status')).toHaveTextContent('2026-09-21');
  expect(screen.getByRole('status')).toHaveTextContent('¥36.00');
  fireEvent.click(screen.getByRole('button', { name: /2026-09-22.*¥96.00/ }));
  expect(screen.getByRole('status')).toHaveTextContent('2026-09-22');
});
it('separates unknown dates into distinct line segments and keeps them focusable', () => {
  const data = snapshot(); data.trend.daily[1] = point(2, null);
  const { container } = render(<CostTrend snapshot={data} />);
  expect(container.querySelectorAll('polyline[data-series="total_cents"]')).toHaveLength(2);
  fireEvent.focus(screen.getByRole('button', { name: /2026-09-22.*待核算/ }));
  expect(screen.getByRole('status')).not.toHaveTextContent('¥0.00');
  expect(container.querySelector('[data-chart-scroll] svg')).toHaveAttribute('viewBox');
  expect(container.querySelector('[data-chart-scroll]')).toHaveClass('overflow-x-auto');
});
it.each([['empty', '暂无费用记录'], ['unpriced_only', '费用待核算，暂无可绘制金额'], ['subscription_only', '订阅覆盖，暂无按量费用曲线']] as const)('does not draw a misleading zero curve for %s', (state, label) => {
  const data = snapshot(); data.summary.display_state = state; data.summary.priced_count = 0;
  const { container } = render(<CostTrend snapshot={data} />);
  expect(screen.getByText(label)).toBeInTheDocument();
  expect(container.querySelector('polyline')).toBeNull();
});
it('keeps all dates focusable in a scrollable long period while using sparse ticks', () => {
  const data = snapshot();
  data.trend.daily = Array.from({ length: 30 }, (_, index) => ({ ...point(1, 4800), date: `2026-09-${String(index + 1).padStart(2, '0')}` }));
  const { container } = render(<CostTrend snapshot={data} />);
  const chart = container.querySelector('[data-chart-scroll] svg')!;
  expect(chart.querySelectorAll('[tabindex="0"]')).toHaveLength(30);
  expect(chart.querySelectorAll('g[role="button"] text').length).toBeLessThanOrEqual(8);
  expect(Number.parseInt((chart as SVGElement).style.minWidth)).toBeGreaterThan(30 * 40);
  expect(chart.querySelector('polyline[data-series="estimated_cents"]')).toHaveAttribute('stroke-dasharray', '6 4');
});
