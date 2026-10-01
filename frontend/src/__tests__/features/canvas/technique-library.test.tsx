import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import i18next from 'i18next';
import { TechniqueLibrary } from '@/features/technique-library/TechniqueLibrary';
import { OperationPanelShell } from '@/features/canvas/ui/OperationPanelShell';
import { createDirectorDraft } from '@/features/canvas/domain/videoDirectorDraft';
import type { DirectorCapabilities, TechniqueCard } from '@/api/videoDirector';
import * as api from '@/api/techniqueLibrary';
import { useAuthStore } from '@/stores/auth-store';
import { useTechniqueLibrary } from '@/features/technique-library/useTechniqueLibrary';
import { durationLabel } from '@/features/technique-library/presentation';
import { TechniqueSketch } from '@/features/technique-library/TechniqueSketch';
vi.mock('@/api/techniqueLibrary', () => ({ getTechniqueCatalog: vi.fn(), getTechniqueFavorites: vi.fn(), setTechniqueFavorite: vi.fn() }));
const i18n = i18next.createInstance();
await i18n.use(initReactI18next).init({ lng: 'zh', resources: { zh: { translation: {} } } });
const card: TechniqueCard = { id: 'static-reaction', version: '1', status: 'active', title: '静止反应', summary: '等待与反应', category: '人物反应', intent: '清晰的情绪', content_hash: 'hash', sources: [],
  applicability: { modes: ['i2v'], min_duration_seconds: 1, max_duration_seconds: 20, last_frame_constraint: 'allowed' } };
const caps: DirectorCapabilities = { models: [], modes: [], referenceLimit: 5, effectiveReferenceLimit: 5, configuredReferenceLimit: 5, fps: 24, frameStep: 17, frameOffset: 5, params: { resolution: [], aspectRatio: [] }, sizes: [] };
function setup(node = <TechniqueLibrary />) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const view = render(<QueryClientProvider client={client}><I18nextProvider i18n={i18n}>{node}</I18nextProvider></QueryClientProvider>);
  return { ...view, client, user: userEvent.setup(), rerender: (next: React.ReactNode) => view.rerender(<QueryClientProvider client={client}><I18nextProvider i18n={i18n}>{next}</I18nextProvider></QueryClientProvider>) };
}
beforeEach(() => {
  vi.clearAllMocks();
  useAuthStore.setState({ username: null });
  vi.mocked(api.getTechniqueCatalog).mockResolvedValue({ catalogVersion: '1', techniques: [card] });
  vi.mocked(api.getTechniqueFavorites).mockResolvedValue({ ids: [] });
  vi.mocked(api.setTechniqueFavorite).mockResolvedValue({ ids: [card.id] });
});
describe('shared technique library', () => {
  it('favorites resolve one latest active version and apply that exact version', async () => {
    vi.mocked(api.getTechniqueCatalog).mockResolvedValue({ catalogVersion: '2', techniques: [
      { ...card, version: '1.9.0', title: '旧版' },
      { ...card, version: '1.10.0', title: '新版' },
      { ...card, version: '2.0.0', status: 'retired', title: '停用版' },
    ] });
    vi.mocked(api.getTechniqueFavorites).mockResolvedValue({ ids: [card.id] });
    const onSelect = vi.fn();
    const { user } = setup(<TechniqueLibrary context={{ segment: createDirectorDraft('s1').segments[0], hasReferences: false, capabilities: caps, onSelect }} />);
    await user.click(screen.getByRole('button', { name: '手法库' }));
    await user.click(await screen.findByRole('button', { name: '☆ 我的收藏' }));
    await user.click(await screen.findByRole('button', { name: '查看 新版' }));
    expect(screen.queryByRole('button', { name: '查看 旧版' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看 停用版' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '应用到当前分段' }));
    expect(onSelect).toHaveBeenCalledExactlyOnceWith({ id: card.id, version: '1.10.0' });
  });
  it('keeps only the latest retired version when no active version exists', async () => {
    vi.mocked(api.getTechniqueCatalog).mockResolvedValue({ catalogVersion: '2', techniques: [
      { ...card, version: '1.0.0', status: 'retired', title: '旧退役' },
      { ...card, version: '2.0.0', status: 'retired', title: '新退役' },
    ] });
    const { user } = setup();
    await user.click(screen.getByRole('button', { name: '手法库' }));
    expect(await screen.findByRole('button', { name: '查看 新退役' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看 旧退役' })).not.toBeInTheDocument();
  });
  it('opens independently of a project, focuses search, filters and resets, then returns focus', async () => {
    const { user } = setup();
    expect(api.getTechniqueCatalog).not.toHaveBeenCalled();
    const trigger = screen.getByRole('button', { name: '手法库' });
    await user.click(trigger);
    const search = await screen.findByRole('searchbox');
    await waitFor(() => expect(search).toHaveFocus());
    await user.type(search, '无结果');
    expect(await screen.findByText('没有匹配的手法')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '重置筛选' }));
    await user.click(screen.getByRole('button', { name: /查看 静止反应/ }));
    expect(screen.queryByRole('button', { name: '应用到当前分段' })).not.toBeInTheDocument();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(trigger).toHaveFocus());
  });
  it('keeps browsing available when favorites fail and supports explicit recovery', async () => {
    vi.mocked(api.getTechniqueFavorites).mockRejectedValueOnce(new Error('offline'));
    const { user } = setup();
    await user.click(screen.getByRole('button', { name: '手法库' }));
    expect(await screen.findByText('收藏读取失败')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /查看 静止反应/ })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: '重试读取收藏' }));
    await waitFor(() => expect(screen.queryByText('收藏读取失败')).not.toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: '收藏 静止反应' }));
    expect(await screen.findByRole('button', { name: '取消收藏 静止反应' })).toBeInTheDocument();
  });
  it('shows incompatible details but cannot apply without capabilities', async () => {
    const onSelect = vi.fn();
    const { user } = setup(<TechniqueLibrary context={{ segment: createDirectorDraft('s1').segments[0], hasReferences: false, capabilities: null, onSelect }} />);
    await user.click(screen.getByRole('button', { name: '手法库' }));
    await user.click(await screen.findByRole('checkbox', { name: '显示全部手法' }));
    await user.click(await screen.findByRole('button', { name: /查看 静止反应/ }));
    expect(screen.getByRole('button', { name: '应用到当前分段' })).toBeDisabled();
    expect(onSelect).not.toHaveBeenCalled();
  });
  it('applies a frozen id/version only to the provided segment callback', async () => {
    const onSelect = vi.fn();
    const { user } = setup(<TechniqueLibrary context={{ segment: createDirectorDraft('s1').segments[0], hasReferences: false, capabilities: caps, onSelect }} />);
    await user.click(screen.getByRole('button', { name: '手法库' }));
    await user.click(await screen.findByRole('button', { name: /查看 静止反应/ }));
    await user.click(screen.getByRole('button', { name: '应用到当前分段' }));
    expect(onSelect).toHaveBeenCalledExactlyOnceWith({ id: card.id, version: card.version });
  });
  it('Escape closes the nested library and preserves the expanded editor', async () => {
    const collapse = vi.fn();
    const { user } = setup(<OperationPanelShell expanded onCollapse={collapse} inlineClassName="" inlineStyle={{}}><TechniqueLibrary /></OperationPanelShell>);
    await user.click(screen.getByRole('button', { name: '手法库' }));
    await screen.findByRole('searchbox');
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(collapse).not.toHaveBeenCalled();
    await user.keyboard('{Escape}');
    expect(collapse).toHaveBeenCalledOnce();
  });
  it('does not mark an unsuccessful favorite saved and lets the user retry', async () => {
    vi.mocked(api.setTechniqueFavorite).mockRejectedValueOnce(new Error('offline'));
    const { user } = setup();
    await user.click(screen.getByRole('button', { name: '手法库' }));
    await user.click(await screen.findByRole('button', { name: '收藏 静止反应' }));
    expect(await screen.findByText('未保存')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '收藏 静止反应' })).toHaveAttribute('aria-pressed', 'false');
    await user.click(screen.getByRole('button', { name: '重试保存' }));
    expect(await screen.findByRole('button', { name: '取消收藏 静止反应' })).toHaveAttribute('aria-pressed', 'true');
  });
  it('re-evaluates the current segment and effective input mode before applying', async () => {
    const original = vi.fn(); const next = vi.fn();
    const segment = createDirectorDraft('s1').segments[0];
    const { user, rerender } = setup(<TechniqueLibrary context={{ segment, hasReferences: false, capabilities: caps, onSelect: original }} />);
    await user.click(screen.getByRole('button', { name: '手法库' }));
    await user.click(await screen.findByRole('button', { name: /查看 静止反应/ }));
    expect(screen.getByRole('button', { name: '应用到当前分段' })).toBeEnabled();
    rerender(<TechniqueLibrary context={{ segment: { ...segment, id: 's2' }, hasReferences: true, capabilities: caps, onSelect: next }} />);
    expect(screen.getByRole('button', { name: '应用到当前分段' })).toBeDisabled();
    expect(original).not.toHaveBeenCalled(); expect(next).not.toHaveBeenCalled();
  });
  it('does not expose a late favorite response after account switching', async () => {
    let resolveOld!: (value: { ids: string[] }) => void;
    useAuthStore.setState({ username: 'alice' });
    vi.mocked(api.getTechniqueFavorites).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    const { user } = setup();
    await user.click(screen.getByRole('button', { name: '手法库' }));
    await screen.findByRole('button', { name: /查看 静止反应/ });
    act(() => useAuthStore.setState({ username: 'bob' }));
    await waitFor(() => expect(api.getTechniqueFavorites).toHaveBeenCalledTimes(2));
    await act(async () => resolveOld({ ids: [card.id] }));
    await waitFor(() => expect(screen.getByRole('button', { name: '收藏 静止反应' })).toBeEnabled());
    expect(screen.queryByRole('button', { name: '取消收藏 静止反应' })).not.toBeInTheDocument();
  });
  it('serializes favorite changes from separate library instances sharing one cache', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: React.PropsWithChildren) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    let completeFirst!: (value: { ids: string[] }) => void;
    vi.mocked(api.setTechniqueFavorite)
      .mockImplementationOnce(() => new Promise((resolve) => { completeFirst = resolve; }))
      .mockResolvedValueOnce({ ids: ['first', 'second'] });
    const first = renderHook(() => useTechniqueLibrary(null), { wrapper });
    const second = renderHook(() => useTechniqueLibrary(null), { wrapper });
    await waitFor(() => expect(first.result.current.favorites.isSuccess).toBe(true));
    act(() => {
      first.result.current.mutation.mutate({ id: 'first', favorite: true });
      second.result.current.mutation.mutate({ id: 'second', favorite: true });
    });
    await waitFor(() => expect(api.setTechniqueFavorite).toHaveBeenCalledTimes(1));
    expect(first.result.current.favorites.data?.ids).toEqual([]);
    await act(async () => completeFirst({ ids: ['first'] }));
    await waitFor(() => expect(second.result.current.favorites.data?.ids).toEqual(['first', 'second']));
    expect(first.result.current.favorites.data?.ids).toEqual(['first', 'second']);
    expect(api.setTechniqueFavorite).toHaveBeenCalledTimes(2);
  });
  it('keeps duration bounds readable and identifies rounding', () => {
    expect(durationLabel(5.875)).toBe('≈5.88');
    expect(durationLabel(12)).toBe('12');
    expect(durationLabel(5.25)).toBe('5.25');
  });
  it('labels retired cards and categories while preserving their expected effect in details', async () => {
    vi.mocked(api.getTechniqueCatalog).mockResolvedValue({ catalogVersion: '1', techniques: [{ ...card, status: 'retired' }] });
    const { user } = setup();
    await user.click(screen.getByRole('button', { name: '手法库' }));
    const preview = await screen.findByRole('button', { name: /查看 静止反应/ });
    expect(within(preview).getByText('已停用')).toBeInTheDocument();
    expect(within(preview).getByText('人物表演')).toBeInTheDocument();
    await user.click(preview);
    const details = screen.getByRole('region', { name: '手法详情' });
    expect(within(details).getByText('已停用')).toBeInTheDocument();
    expect(within(details).getByText(card.summary)).toBeInTheDocument();
  });
  it('disables cached detail application when a catalog refresh fails', async () => {
    const onSelect = vi.fn();
    const { user, client } = setup(<TechniqueLibrary context={{ segment: createDirectorDraft('s1').segments[0], hasReferences: false, capabilities: caps, onSelect }} />);
    await user.click(screen.getByRole('button', { name: '手法库' }));
    await user.click(await screen.findByRole('button', { name: /查看 静止反应/ }));
    vi.mocked(api.getTechniqueCatalog).mockRejectedValueOnce(new Error('offline'));
    await act(async () => { await client.invalidateQueries({ queryKey: ['technique-catalog'] }); });
    await waitFor(() => expect(screen.getByRole('button', { name: '应用到当前分段' })).toBeDisabled());
    expect(onSelect).not.toHaveBeenCalled();
  });
  it('uses an honest text fallback for techniques without an original sketch', () => {
    render(<I18nextProvider i18n={i18n}><TechniqueSketch id="unknown-future-technique" /></I18nextProvider>);
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.getByText('此手法暂无镜头示意，请参阅文字说明')).toBeInTheDocument();
  });
});
