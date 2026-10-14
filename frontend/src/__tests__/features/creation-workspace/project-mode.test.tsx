import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { expect, it, vi } from 'vitest';
import ky from 'ky';
import { server } from '@/__mocks__/msw/server';
import { useAllProjectSummaries } from '@/lib/queries/projects';
vi.mock('@/lib/api', () => ({ api: ky.create({ baseUrl: 'http://localhost/' }) }));

it('keeps server creation_mode through the project-summary adapter', async () => {
  server.use(http.get('http://localhost/api/v1/projects/summaries', () => HttpResponse.json({ ok: true, data: [
    { id: 'canvas-project', name: '新项目', status: 'active', creation_mode: 'agent_canvas_v1' },
    { id: 'legacy-project', name: '旧项目', status: 'active' },
  ] })));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const hook = renderHook(useAllProjectSummaries, { wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> });
  await waitFor(() => expect(hook.result.current.data).toHaveLength(2));
  expect(hook.result.current.data?.[0].creationMode).toBe('agent_canvas_v1');
  expect(hook.result.current.data?.[1].creationMode).toBeUndefined();
});
