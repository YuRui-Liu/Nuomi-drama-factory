import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeAll, expect, it, vi } from 'vitest';
import ky from 'ky';
import i18next from 'i18next';
import { initReactI18next } from 'react-i18next';
import { http, HttpResponse } from 'msw';
import { server } from '@/__mocks__/msw/server';
import { useAuthStore } from '@/stores/auth-store';
import { CostSettingsActions, yuanMicros } from './cost-settings';
import { microsYuan } from './cost-settings-common';
it('round trips stored micros without floating point arithmetic', () => { expect(microsYuan(9007199254740991)).toBe('9007199254.740991'); expect(yuanMicros(microsYuan(9007199254740991))).toBe(9007199254740991); });
vi.mock('@/lib/api', () => ({ api: ky.create({ baseUrl: 'http://localhost:3000/' }) }));
beforeAll(async () => { await i18next.use(initReactI18next).init({ lng: 'zh', resources: {} }); });
afterEach(() => { cleanup(); useAuthStore.setState({ role: null }); });
function setup(role = 'owner') { useAuthStore.setState({ role }); const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }); render(<QueryClientProvider client={client}><CostSettingsActions project="one"/></QueryClientProvider>); return client; }
it('keeps global settings closed and unavailable to members', () => { setup('member'); expect(screen.queryByRole('button', { name: '计价规则' })).toBeNull(); expect(screen.getByRole('button', { name: '补算预览' })).toBeInTheDocument(); });
it('converts decimal yuan exactly and rejects invalid values', () => { expect(yuanMicros('')).toBeNull(); expect(yuanMicros('1.000001')).toBe(1000001); expect(() => yuanMicros('-1')).toThrow(); expect(() => yuanMicros('9007199254.740992')).toThrow(); });
it('lazily fetches rules, validates numbers, preserves decimal strings, and surfaces version conflicts', async () => {
    let reads = 0;
    let body: unknown;
    server.use(http.get('*/api/v1/cost-settings/price-rules', () => { reads++; return HttpResponse.json({ ok: true, data: [] }); }), http.post('*/api/v1/cost-settings/price-rules', async ({ request }) => { body = await request.json(); return HttpResponse.json({ detail: 'overlap' }, { status: 409 }); }));
    setup();
    expect(reads).toBe(0);
    fireEvent.click(screen.getByRole('button', { name: '计价规则' }));
    fireEvent.click(screen.getByText('高级：自定义计价规则'));
    await screen.findByLabelText('规则 ID');
    fireEvent.change(screen.getByLabelText('规则 ID'), { target: { value: 'rule' } });
    fireEvent.change(screen.getByLabelText('版本'), { target: { value: 'v1' } });
    fireEvent.change(screen.getByLabelText('单价'), { target: { value: '-1' } });
    fireEvent.click(screen.getByRole('button', { name: '创建价格版本' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('单价必填且非负');
    expect(body).toBeUndefined();
    fireEvent.change(screen.getByLabelText('单价'), { target: { value: '0.000000123' } });
    fireEvent.click(screen.getByRole('button', { name: '创建价格版本' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('冲突');
    expect(body).toMatchObject({ id: 'rule', version: 'v1', items: [{ unit: 'item', unit_price: '0.000000123', basis: '1', step: '1', minimum: '0' }], cny_rate: null });
});
it('refreshes all project query families only after explicit successful apply', async () => {
    let applies = 0;
    server.use(http.post('*/api/v1/projects/one/costs/reprice-preview', () => HttpResponse.json({ ok: true, data: { preview_id: 'p', affected_count: 1, total_cents: 1, records: [{ attempt_id: 'x', old_cents: null, new_cents: 1, matched_rule_versions: [] }] } })), http.post('*/api/v1/projects/one/costs/reprice-apply', () => { applies++; return HttpResponse.json({ ok: true, data: {} }); }));
    const client = setup();
    for (const type of ['snapshot', 'entries', 'entry'])
        client.setQueryData(['project-costs', 'one', type], {});
    fireEvent.click(screen.getByRole('button', { name: '补算预览' }));
    await screen.findByText('x');
    expect(applies).toBe(0);
    fireEvent.click(screen.getByRole('button', { name: '应用补算' }));
    await screen.findByRole('status');
    for (const query of client.getQueryCache().findAll({ queryKey: ['project-costs', 'one'] }))
        expect(query.state.isInvalidated).toBe(true);
});
it('saves unknown subscription amount as null', async () => { let body: unknown; server.use(http.get('*/api/v1/cost-settings/subscriptions', () => HttpResponse.json({ ok: true, data: [] })), http.post('*/api/v1/cost-settings/subscriptions', async ({ request }) => { body = await request.json(); return HttpResponse.json({ ok: true, data: null }); })); setup(); fireEvent.click(screen.getByRole('button', { name: '费用设置' })); fireEvent.change(await screen.findByLabelText('订阅 ID'), { target: { value: 'sub' } }); fireEvent.change(screen.getByLabelText('渠道'), { target: { value: 'p' } }); fireEvent.change(screen.getByLabelText('账户别名'), { target: { value: 'a' } }); fireEvent.click(screen.getByRole('button', { name: '保存订阅' })); await waitFor(() => expect(body).toMatchObject({ id: 'sub', amount_micros: null })); });
it('requires explicit apply, retains stale preview, and never auto applies regeneration', async () => { let applies = 0; let previews = 0; server.use(http.post('*/api/v1/projects/one/costs/reprice-preview', () => HttpResponse.json({ ok: true, data: { preview_id: `p${++previews}`, project_id: 'one', affected_count: 1, total_cents: 120, records: [{ attempt_id: 'a', old_cents: null, new_cents: 120, matched_rule_versions: [{ id: 'r', version: '1' }] }] } })), http.post('*/api/v1/projects/one/costs/reprice-apply', () => { applies++; return HttpResponse.json({ detail: 'stale' }, { status: 409 }); })); setup('member'); fireEvent.click(screen.getByRole('button', { name: '补算预览' })); await screen.findByText('a'); expect(applies).toBe(0); fireEvent.click(screen.getByRole('button', { name: '应用补算' })); expect(await screen.findByRole('alert')).toHaveTextContent('重新生成'); expect(screen.getByText('a')).toBeInTheDocument(); fireEvent.click(screen.getByRole('button', { name: '重新生成预览' })); await waitFor(() => expect(previews).toBe(2)); expect(applies).toBe(1); });
