import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createBrowserHistory, createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider, useParams } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { server } from '@/__mocks__/msw/server';
import { CreationWorkspace } from '@/features/creation-workspace/CreationWorkspace';
import { registerFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
import { useEffect } from 'react';
import userEvent from '@testing-library/user-event';
import { creationCanvasesKey } from '@/lib/queries/creation';
const shellState = vi.hoisted(() => ({ unhydratedError: false }));

vi.mock('@/api/client', async (original) => {
  const actual = await original<typeof import('@/api/client')>();
  return { ...actual, apiCall: (path: string, options?: Parameters<typeof actual.apiCall>[1]) => actual.apiCall(path, { ...options, prefix: 'http://localhost/api/v1' }) };
});
vi.mock('@/features/freezone/FreezoneShell', () => ({
  FreezoneShell: ({ canvasId, managedWorkspace, onWorkspaceState }: { canvasId: string; managedWorkspace?: boolean; onWorkspaceState?: (state: { canvasId: string; hydrated: boolean; status: 'error' }) => void }) => {
    useEffect(() => { if (shellState.unhydratedError) onWorkspaceState?.({ canvasId, hydrated: false, status: 'error' }); }, [canvasId, onWorkspaceState]);
    return <div data-testid="canvas">{canvasId}:{String(managedWorkspace)}</div>;
  },
}));

const base = 'http://localhost/api/v1/projects/p/creation';
const records = [
  { canvas_id: 'concept-server', kind: 'concept', episode_id: null, title: '构思一', status: 'applied' },
  { canvas_id: 'episode-server', kind: 'episode', episode_id: 'ep-semantic', title: '第一集', status: 'applied' },
  { canvas_id: 'free-server', kind: 'free', episode_id: null, title: '自由一', status: 'applied' },
];
let dispose: (() => void) | undefined;
beforeEach(() => {
  shellState.unhydratedError = false;
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
  server.use(http.get(`${base}/canvases`, () => HttpResponse.json({ ok: true, data: records })));
  dispose = registerFreezoneCanvasRuntime('p', 'concept-server', () => {}, async () => true);
});
afterEach(() => { cleanup(); dispose?.(); });

async function mount(search = '', browser = false) {
  const root = createRootRoute();
  const route = createRoute({ getParentRoute: () => root, path: '/projects/$project/freezone', component: () => {
    const { project = 'p' } = useParams({ strict: false });
    return <CreationWorkspace key={project} project={{ id: project, name: '项目', creation_mode: 'canvas' }} />;
  } });
  const assets = createRoute({ getParentRoute: () => root, path: '/projects/$project/characters', component: () => <div>素材页面</div> });
  if (browser) window.history.replaceState(null, '', `/projects/p/freezone${search}`);
  const history = browser ? createBrowserHistory() : createMemoryHistory({ initialEntries: [`/projects/p/freezone${search}`] });
  const router = createRouter({ routeTree: root.addChildren([route, assets]), history });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  await act(async () => { await router.load(); });
  return { router, history, client };
}

function gate() {
  let resolve!: () => void;
  const promise = new Promise<void>(done => { resolve = done; });
  return { promise, resolve };
}

it.each(['success', 'concept_exists'])('does not let a late %s creation reclaim navigation after leaving or switching', async response => {
  for (const destination of ['assets', 'project', 'canvas']) {
    const pending = gate();
    let started = false;
    server.use(http.get('http://localhost/api/v1/projects/other/creation/canvases', () => HttpResponse.json({ ok: true, data: records })),
      http.post(`${base}/canvases`, async () => {
        started = true; await pending.promise;
        return response === 'success' ? HttpResponse.json({ ok: true, data: { ...records[2], canvas_id: 'late-canvas', operation_id: 'op' } }) : HttpResponse.json({ detail: { code: 'concept_exists', canvas_id: 'concept-server' } }, { status: 409 });
      }));
    dispose?.(); dispose = registerFreezoneCanvasRuntime('p', 'concept-server', () => {}, async () => true);
    const { router, client } = await mount('?workspace=concept-server');
    fireEvent.change(await screen.findByLabelText('画布名称'), { target: { value: '慢请求' } });
    if (response === 'success') fireEvent.change(screen.getByLabelText('画布类型'), { target: { value: 'free' } });
    fireEvent.click(screen.getByRole('button', { name: '创建画布' }));
    await waitFor(() => expect(started).toBe(true));
    await act(async () => {
      if (destination === 'assets') await router.navigate({ to: '/projects/$project/characters', params: { project: 'p' } });
      else await router.navigate({ to: '/projects/$project/freezone', params: { project: destination === 'project' ? 'other' : 'p' }, search: { workspace: destination === 'canvas' ? 'free-server' : 'concept-server' } });
    });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe(destination === 'assets' ? '/projects/p/characters' : `/projects/${destination === 'project' ? 'other' : 'p'}/freezone`);
      if (destination === 'canvas') expect(router.state.location.search).toMatchObject({ workspace: 'free-server' });
    });
    const href = router.state.location.href;
    dispose?.(); dispose = registerFreezoneCanvasRuntime(destination === 'project' ? 'other' : 'p', destination === 'canvas' ? 'free-server' : 'concept-server', () => {}, async () => true);
    await act(async () => { pending.resolve(); });
    await waitFor(() => expect(client.isMutating()).toBe(0));
    await act(async () => {});
    expect(router.state.location.href).toBe(href);
    if (response === 'success') expect(client.getQueryData(creationCanvasesKey('p'))).toEqual(expect.arrayContaining([expect.objectContaining({ canvas_id: 'late-canvas' })]));
    cleanup(); client.clear();
  }
});

it('cancels an older in-flight registry GET before publishing the created workspace', async () => {
  const pending = gate(); const returned = gate();
  let started = false;
  let aborted = false;
  const { client, router } = await mount('?workspace=concept-server');
  await screen.findByTestId('canvas');
  server.use(http.get(`${base}/canvases`, async ({ request }) => {
    started = true;
    request.signal.addEventListener('abort', () => { aborted = true; });
    await pending.promise; returned.resolve();
    return HttpResponse.json({ ok: true, data: records });
  }), http.post(`${base}/canvases`, () => HttpResponse.json({ ok: true, data: { ...records[2], canvas_id: 'created-during-refetch', operation_id: 'op' } })));
  let refetch!: Promise<void>;
  act(() => { refetch = client.refetchQueries({ queryKey: creationCanvasesKey('p') }); });
  await waitFor(() => expect(started).toBe(true));
  fireEvent.change(screen.getByLabelText('画布名称'), { target: { value: '新画布' } });
  fireEvent.change(screen.getByLabelText('画布类型'), { target: { value: 'free' } });
  fireEvent.click(screen.getByRole('button', { name: '创建画布' }));
  await waitFor(() => expect(router.state.location.search).toMatchObject({ workspace: 'created-during-refetch' }));
  await act(async () => { pending.resolve(); await returned.promise; await refetch; });
  expect(aborted).toBe(true);
  expect(client.getQueryData(creationCanvasesKey('p'))).toEqual(expect.arrayContaining([expect.objectContaining({ canvas_id: 'created-during-refetch' })]));
  expect(screen.getByTestId('canvas')).toHaveTextContent('created-during-refetch');
  expect(screen.queryByText('画布不存在')).not.toBeInTheDocument();
});

it('lists all server canvas kinds and defaults to the registered concept', async () => {
  await mount('?canvas=old-personal');
  expect(await screen.findByTestId('canvas')).toHaveTextContent('concept-server:true');
  for (const title of ['构思一', '第一集', '自由一']) expect(screen.getByRole('button', { name: new RegExp(title) })).toBeVisible();
});
it('searches, collapses groups, and switches with the keyboard after exactly one flush', async () => {
  const flush = vi.fn(async () => true);
  dispose?.(); dispose = registerFreezoneCanvasRuntime('p', 'concept-server', () => {}, flush);
  const { router } = await mount('?workspace=concept-server');
  const user = userEvent.setup();
  await screen.findByRole('button', { name: '自由一' });
  await user.type(screen.getByLabelText('搜索画布'), '自由');
  expect(screen.queryByRole('button', { name: '构思一' })).not.toBeInTheDocument();
  const group = screen.getByRole('button', { name: '自由画布' });
  await user.click(group);
  expect(screen.queryByRole('button', { name: '自由一' })).not.toBeInTheDocument();
  await user.keyboard('{Enter}');
  screen.getByRole('button', { name: '自由一' }).focus();
  await user.keyboard('{Enter}');
  await waitFor(() => expect(router.state.location.search).toMatchObject({ workspace: 'free-server' }));
  expect(flush).toHaveBeenCalledTimes(1);
});
it('resolves episode compatibility by semantic ID, never by canvas ID', async () => {
  const { router } = await mount('?episode=ep-semantic');
  expect(await screen.findByTestId('canvas')).toHaveTextContent('episode-server');
  await waitFor(() => expect(router.state.location.search).toMatchObject({ workspace: 'episode-server' }));
});
it.each(['?workspace=unregistered', '?workspace=123', '?workspace=0', '?workspace=', '?episode=1'])('does not hydrate or silently create an unknown workspace %s', async search => {
  await mount(search);
  expect(await screen.findByText('画布不存在')).toBeVisible();
  expect(screen.queryByTestId('canvas')).not.toBeInTheDocument();
});
it('keeps the current canvas and URL when flushing fails', async () => {
  dispose?.(); dispose = registerFreezoneCanvasRuntime('p', 'concept-server', () => {}, async () => false);
  const { router } = await mount('?workspace=concept-server');
  fireEvent.click(await screen.findByRole('button', { name: /自由一/ }));
  expect(await screen.findByRole('alert')).toHaveTextContent('未能保存');
  expect(screen.getByTestId('canvas')).toHaveTextContent('concept-server');
  expect(router.state.location.search).toMatchObject({ workspace: 'concept-server' });
});
it('keeps edits when a runtime has not registered yet', async () => {
  dispose?.();
  await mount('?workspace=concept-server');
  fireEvent.click(await screen.findByRole('button', { name: /自由一/ }));
  expect(await screen.findByRole('alert')).toHaveTextContent('尚未就绪');
  expect(screen.getByTestId('canvas')).toHaveTextContent('concept-server');
});
it('allows leaving a failed initial hydration without unsaved edits', async () => {
  shellState.unhydratedError = true;
  dispose?.(); dispose = registerFreezoneCanvasRuntime('p', 'concept-server', () => {}, async () => false);
  const { router } = await mount('?workspace=concept-server');
  fireEvent.click(await screen.findByRole('button', { name: /自由一/ }));
  await waitFor(() => expect(router.state.location.search).toMatchObject({ workspace: 'free-server' }));
});
it('shows legacy projects as read-only with a new-project entry', async () => {
  server.use(http.get(`${base}/canvases`, () => HttpResponse.json({ detail: { code: 'legacy_project_readonly' } }, { status: 409 })));
  await mount();
  expect(await screen.findByText('旧项目仅供查看')).toBeVisible();
  expect(screen.getByRole('link', { name: '新建项目' })).toHaveAttribute('href', '/');
  expect(screen.queryByTestId('canvas')).not.toBeInTheDocument();
});
it.each(['free', 'episode'])('creates %s without an existing episode, using the server ID', async (kind) => {
  const requests: unknown[] = [];
  server.use(http.get(`${base}/canvases`, () => HttpResponse.json({ ok: true, data: [] })),
    http.post(`${base}/${kind === 'episode' ? 'episodes' : 'canvases'}`, async ({ request }) => {
      requests.push(await request.json());
      return HttpResponse.json({ ok: true, data: { canvas_id: 'created-server', episode_id: kind === 'episode' ? 'new-semantic' : null, kind, title: '新创作', status: 'applied', operation_id: 'op', number: 1, revision: 1 } });
    }));
  await mount();
  fireEvent.change(await screen.findByLabelText('画布类型'), { target: { value: kind } });
  fireEvent.change(screen.getByLabelText('画布名称'), { target: { value: '新创作' } });
  fireEvent.click(screen.getByRole('button', { name: '创建画布' }));
  expect(await screen.findByTestId('canvas')).toHaveTextContent('created-server');
  expect(screen.getByLabelText('画布名称')).toHaveValue('');
  expect(requests).toEqual([expect.objectContaining({ title: '新创作', request_id: expect.any(String) })]);
});
it.each(['running', 'conflict'])('does not load an incomplete %s creation', async (status) => {
  server.use(http.get(`${base}/canvases`, () => HttpResponse.json({ ok: true, data: [] })),
    http.post(`${base}/canvases`, () => HttpResponse.json({ ok: true, data: { ...records[2], status, operation_id: 'op' } })));
  await mount();
  fireEvent.change(await screen.findByLabelText('画布名称'), { target: { value: '自由一' } });
  fireEvent.change(screen.getByLabelText('画布类型'), { target: { value: 'free' } });
  fireEvent.click(screen.getByRole('button', { name: '创建画布' }));
  expect(await screen.findByText(status === 'running' ? '画布正在创建' : '画布创建存在冲突')).toBeVisible();
  expect(screen.queryByTestId('canvas')).not.toBeInTheDocument();
});
it('reuses request ID and payload after a network failure', async () => {
  const bodies: unknown[] = [];
  server.use(http.get(`${base}/canvases`, () => HttpResponse.json({ ok: true, data: [] })),
    http.post(`${base}/canvases`, async ({ request }) => {
      bodies.push(await request.json());
      return bodies.length === 1 ? HttpResponse.error() : HttpResponse.json({ ok: true, data: { ...records[2], operation_id: 'op' } });
    }));
  await mount();
  fireEvent.change(await screen.findByLabelText('画布类型'), { target: { value: 'free' } });
  fireEvent.change(screen.getByLabelText('画布名称'), { target: { value: '重试画布' } });
  fireEvent.click(screen.getByRole('button', { name: '创建画布' }));
  fireEvent.click(await screen.findByRole('button', { name: '重试创建' }));
  expect(await screen.findByTestId('canvas')).toHaveTextContent('free-server');
  expect(bodies).toHaveLength(2); expect(bodies[1]).toEqual(bodies[0]);
});
it('opens the canonical concept when the server reports concept_exists', async () => {
  server.use(http.post(`${base}/canvases`, () => HttpResponse.json({ detail: { code: 'concept_exists', canvas_id: 'concept-server' } }, { status: 409 })));
  const { router } = await mount('?workspace=free-server');
  dispose?.(); dispose = registerFreezoneCanvasRuntime('p', 'free-server', () => {}, async () => true);
  fireEvent.change(await screen.findByLabelText('画布名称'), { target: { value: '另一个构思' } });
  fireEvent.click(screen.getByRole('button', { name: '创建画布' }));
  await waitFor(() => expect(router.state.location.search).toMatchObject({ workspace: 'concept-server' }));
});
it('guards browser back through the same flush contract', async () => {
  const { router, history } = await mount('?workspace=concept-server', true);
  fireEvent.click(await screen.findByRole('button', { name: /自由一/ }));
  await waitFor(() => expect(router.state.location.search).toMatchObject({ workspace: 'free-server' }));
  dispose?.(); dispose = registerFreezoneCanvasRuntime('p', 'free-server', () => {}, async () => false);
  await waitFor(() => expect(screen.getByTestId('canvas')).toHaveTextContent('free-server'));
  act(() => history.back());
  expect(await screen.findByRole('alert')).toHaveTextContent('未能保存');
  expect(screen.getByTestId('canvas')).toHaveTextContent('free-server');
  history.destroy();
});
