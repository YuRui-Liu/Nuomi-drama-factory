import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeAll, expect, it, vi } from 'vitest';
import ky from 'ky';
import i18next from 'i18next';
import { initReactI18next } from 'react-i18next';
import { http, HttpResponse } from 'msw';
import { server } from '@/__mocks__/msw/server';
import { CostRules } from './cost-rules';
import { CostPresets } from './cost-presets';

vi.mock('@/lib/api', () => ({ api: ky.create({ baseUrl: 'http://localhost:3000/' }) }));
beforeAll(async () => { await i18next.use(initReactI18next).init({ lng: 'zh', resources: {} }); });
afterEach(cleanup);

it('shows server platform presets and applies them without a manual form', async () => {
    let installs = 0;
    const data = { platforms: [{ provider: 'runninghub', title: 'RunningHub', description: '优先记录实际 RH 币' }], rules: [{ id: 'grsai', model: 'gpt-image-2', price: '0.03', currency: 'CNY', unit: 'call', source_url: 'https://grsai.com/zh/dashboard/models' }], installed: false };
    server.use(http.get('*/api/v1/cost-settings/presets', () => HttpResponse.json({ ok: true, data })), http.get('*/api/v1/cost-settings/price-rules', () => HttpResponse.json({ ok: true, data: [] })), http.post('*/api/v1/cost-settings/presets', () => { installs++; data.installed = true; return HttpResponse.json({ ok: true, data }); }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    client.setQueryData(['project-costs', 'one', 'snapshot'], {});
    render(<QueryClientProvider client={client}><CostRules /></QueryClientProvider>);
    expect(await screen.findByText('gpt-image-2')).toBeVisible();
    expect(screen.getByText('优先记录实际 RH 币')).toBeVisible();
    expect(screen.getByText('高级：自定义计价规则').closest('details')).not.toHaveAttribute('open');
    expect(installs).toBe(0);
    fireEvent.click(screen.getByRole('button', { name: '一键使用推荐计费' }));
    await screen.findByRole('status');
    await waitFor(() => expect(screen.getByRole('button', { name: '推荐计费已启用' })).toBeDisabled());
    expect(installs).toBe(1);
    expect(client.getQueryState(['project-costs', 'one', 'snapshot'])?.isInvalidated).toBe(true);
});

it('syncs historical usage after installation and preserves custom-rule feedback', async () => {
    let recovered = 0;
    server.use(http.get('*/api/v1/cost-settings/presets', () => HttpResponse.json({ ok: true, data: { platforms: [], rules: [{ id: 'rh', model: 'RunningHub', price: '1', currency: 'RH_CREDIT', unit: 'credit', source_url: 'https://www.runninghub.cn/' }], installed: false } })), http.post('*/api/v1/cost-settings/presets', () => HttpResponse.json({ ok: true, data: { skipped: ['custom'], installed: false } })), http.post('*/api/v1/projects/one/costs/recover', () => { recovered++; return HttpResponse.json({ ok: true, data: { imported: 2 } }); }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><CostPresets project="one" /></QueryClientProvider>);
    expect(await screen.findByText('按实际扣除 RH 币')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '一键使用推荐计费' }));
    expect(await screen.findByText('已同步可恢复的消费，缺失历史凭据的费用仍待核算。')).toBeVisible();
    expect(screen.getByText('已有自定义计价的项目已跳过，保留原有规则。')).toBeVisible();
    expect(recovered).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: '同步历史消费' }));
    await waitFor(() => expect(recovered).toBe(2));
});
