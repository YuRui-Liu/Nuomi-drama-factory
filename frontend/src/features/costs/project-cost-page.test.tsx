// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeAll, expect, it, vi } from 'vitest';
import ky from 'ky';
import i18next from 'i18next';
import { initReactI18next } from 'react-i18next';
import { http, HttpResponse } from 'msw';
import { server } from '@/__mocks__/msw/server';
import { ProjectCostPage } from './project-cost-page';
vi.mock('@/lib/api', () => ({ api: ky.create({ baseUrl: 'http://localhost:3000/' }) }));
beforeAll(async () => { await i18next.use(initReactI18next).init({ lng: 'zh', resources: {}, interpolation: { escapeValue: false } }); });

afterEach(cleanup);
const bucket = { total_cents: 1234, confirmed_cents: 1000, estimated_cents: 234, priced_count: 2, unpriced_count: 1, pending_count: 0, subscription_count: 0, attempt_count: 3 };
function snapshot(project = 'one', unknown = false) {
  const b = unknown ? { ...bucket, priced_count: 0, total_cents: 0, confirmed_cents: 0, estimated_cents: 0 } : bucket;
  return { project_id: project, currency: 'CNY', timezone: 'Asia/Shanghai', snapshot_at: '2026-09-24T10:00:00+08:00', range: { from: '2026-09-01T00:00:00+08:00', to: '2026-09-24T10:00:00+08:00' }, summary: { ...b, complete: false, display_state: unknown ? 'unpriced_only' : 'priced' }, coverage: { complete: false, start_at: null, reasons: ['unpriced_attempts'], gaps: [] }, trend: { daily: [], cumulative: [] }, subscriptions: [], breakdown: { channels: [{ provider: 'runninghub', ...b, media: [{ media_type: 'image', ...b }] }], media: [{ media_type: 'image', ...b }] } };
}
function setup(unknown = false) {
  const requests: string[] = [];
  server.use(http.get('*/api/v1/projects/:project/costs/snapshot', ({ params }) => HttpResponse.json({ ok: true, data: snapshot(String(params.project), unknown) })), http.get('*/api/v1/projects/:project/costs/entries', ({ request }) => { requests.push(request.url); return HttpResponse.json({ ok: true, data: { entries: [], next_cursor: null } }); }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = (project: string) => <QueryClientProvider client={client}><ProjectCostPage project={project} /></QueryClientProvider>;
  return { ...render(ui('one')), requests, client, ui };
}
it('renders known subtotal and filters only the ledger from a matrix cell', async () => {
  const { requests, client } = setup();
  expect(await screen.findByTestId('cost-total')).toHaveTextContent('12.34');
  fireEvent.click(screen.getByRole('button', { name: /runninghub.*图片/ }));
  await waitFor(() => expect(requests.some(url => url.includes('channel=runninghub') && url.includes('media=image'))).toBe(true));
  expect(client.getQueryCache().findAll({ queryKey: ['project-costs', 'one', 'snapshot'] })).toHaveLength(1);
  expect(screen.getByTestId('cost-total')).toHaveTextContent('12.34');
});
it('never presents all-unpriced totals as free', async () => {
  setup(true);
  expect(await screen.findByTestId('cost-total')).toHaveTextContent('待核算');
  expect(screen.getByTestId('cost-total')).not.toHaveTextContent('0.00');
});
it('resets ledger filters when the project changes', async () => {
  const { requests, rerender, ui } = setup();
  await screen.findByTestId('cost-total');
  fireEvent.click(screen.getByRole('button', { name: /runninghub.*图片/ }));
  rerender(ui('two'));
  await waitFor(() => expect(requests.some(url => url.includes('/two/costs/entries') && !url.includes('channel='))).toBe(true));
});
it('rejects error envelopes and offers retry', async () => {
  setup();
  await screen.findByTestId('cost-total');
  server.use(http.get('*/api/v1/projects/:project/costs/snapshot', () => HttpResponse.json({ ok: false, error: 'offline' })));
  fireEvent.click(screen.getByRole('button', { name: '刷新' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('刷新失败');
  expect(screen.getByTestId('cost-total')).toHaveTextContent('12.34');
});
it('shows retry for first-load failure without a zero total', async () => {
  const { client } = setup();
  server.use(http.get('*/api/v1/projects/:project/costs/snapshot', () => HttpResponse.json({ ok: false, error: 'unavailable' })));
  await client.cancelQueries();
  await client.resetQueries();
  expect(await screen.findByRole('alert')).toHaveTextContent('费用加载失败');
  expect(screen.queryByTestId('cost-total')).not.toBeInTheDocument();
  server.use(http.get('*/api/v1/projects/:project/costs/snapshot', () => HttpResponse.json({ ok: true, data: snapshot() })));
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(await screen.findByTestId('cost-total')).toHaveTextContent('12.34');
});
it('resets pagination when filters change and renders audited detail', async () => {
  const { requests } = setup();
  const attempt = { attempt_id: 'attempt-one', project_id: 'one', provider: 'runninghub', account_id: 'main', model: 'test-model', media_type: 'image', occurred_at: '2026-09-24T02:00:00Z', task_id: 'task-one', resource_id: null, external_id: 'external-one', execution_status: 'succeeded', submission_status: 'submitted', usage: { item: '1' }, usage_source: 'provider', workflow: null, specifications: [] };
  const value = { status: 'estimated', amount_micros: 2340000, reason: 'rule matched' };
  server.use(http.get('*/api/v1/projects/:project/costs/entries', ({ request }) => { requests.push(request.url); return HttpResponse.json({ ok: true, data: { entries: [{ ...attempt, value, amount_cents: 234, cost_status: 'estimated' }], next_cursor: new URL(request.url).searchParams.has('cursor') ? null : 'page-two' } }); }), http.get('*/api/v1/projects/:project/costs/entries/attempt-one', () => HttpResponse.json({ ok: true, data: { attempt, value, current_cost: { value, amount_cents: 234, evidence: { source: 'price rule' }, rule_snapshot: { id: 'image-price', version: 'v1', currency: 'CNY', cny_rate: null, items: [{ unit: 'item', unit_price: '2.34', basis: '1', step: '1', minimum: '0' }] } }, revisions: [{ value, sequence: 1, applied: true, recorded_at: '2026-09-24T02:00:00Z' }] } })));
  await screen.findByRole('button', { name: '查看' });
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  await waitFor(() => expect(requests.some(url => url.includes('cursor=page-two'))).toBe(true));
  fireEvent.change(screen.getByLabelText('费用状态'), { target: { value: 'estimated' } });
  await waitFor(() => expect(requests.some(url => url.includes('status=estimated') && !url.includes('cursor='))).toBe(true));
  fireEvent.click(await screen.findByRole('button', { name: '查看' }));
  expect(await screen.findByText('匹配价格规则：image-price / v1')).toBeInTheDocument();
  expect(screen.getByText('external-one')).toBeInTheDocument();
  expect(screen.getByText('费用修订历史')).toBeInTheDocument();
});
it('refreshes the ledger and open detail with snapshot refreshes', async () => {
  const { client } = setup();
  let revision = 1;
  const attempt = () => ({ attempt_id: 'live', project_id: 'one', provider: 'runninghub', account_id: 'main', model: `model-${revision}`, media_type: 'image', occurred_at: '2026-09-24T02:00:00Z', task_id: null, resource_id: null, external_id: `external-${revision}`, execution_status: 'succeeded', submission_status: 'submitted', usage: { call: '1' }, usage_source: 'request', workflow: null, specifications: [] });
  const value = () => ({ status: 'confirmed', amount_micros: revision * 1000000, reason: null });
  server.use(
    http.get('*/api/v1/projects/:project/costs/snapshot', () => HttpResponse.json({ ok: true, data: { ...snapshot(), summary: { ...snapshot().summary, total_cents: revision * 100 } } })),
    http.get('*/api/v1/projects/:project/costs/entries', () => HttpResponse.json({ ok: true, data: { entries: [{ ...attempt(), value: value(), amount_cents: revision * 100, cost_status: 'confirmed' }], next_cursor: null } })),
    http.get('*/api/v1/projects/:project/costs/entries/live', () => HttpResponse.json({ ok: true, data: { attempt: attempt(), value: value(), current_cost: { value: value(), amount_cents: revision * 100 }, revisions: [] } })),
  );
  await screen.findByText('model-1');
  revision = 2;
  fireEvent.click(screen.getByRole('button', { name: '刷新' }));
  expect(await screen.findByText('model-2')).toBeInTheDocument();
  expect(screen.getByTestId('cost-total')).toHaveTextContent('2.00');
  fireEvent.click(screen.getByRole('button', { name: '查看' }));
  expect(await screen.findByText('external-2')).toBeInTheDocument();
  expect(screen.getByText('已提交')).toBeInTheDocument();
  expect(screen.getByText('执行成功')).toBeInTheDocument();
  expect(screen.getAllByText('请求参数').length).toBeGreaterThan(0);
  revision = 3;
  // The same snapshot query is refetched by the 15-second foreground timer.
  await act(async () => { await client.refetchQueries({ queryKey: ['project-costs', 'one', 'snapshot'] }); });
  expect(await screen.findByText('external-3')).toBeInTheDocument();
  expect(screen.getAllByText('model-3')).toHaveLength(2);
});
