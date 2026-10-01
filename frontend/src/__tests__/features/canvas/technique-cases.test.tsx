import { expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { CaseBrowser } from '@/features/technique-library/CaseBrowser';
import * as api from '@/api/techniqueLibrary';
import { TechniqueLibrary } from '@/features/technique-library/TechniqueLibrary';
import { createDirectorDraft } from '@/features/canvas/domain/videoDirectorDraft';
import type { DirectorCapabilities } from '@/api/videoDirector';
vi.mock('@/api/techniqueLibrary', () => ({ getTechniqueCases: vi.fn(), getTechniqueCase: vi.fn(), getTechniqueCatalog: vi.fn(), getTechniqueFavorites: vi.fn(), setTechniqueFavorite: vi.fn() }));
import { categoryOf } from '@/features/technique-library/presentation';
import type { TechniqueCard } from '@/api/videoDirector';

it('does not classify unfamiliar cinematography as performance', () => {
  expect(categoryOf({ id: 'new', category: 'new cinematography' } as TechniqueCard)).toBe('other');
});

it('opens from a primary mouse pointer sequence while isolating canvas pointer and click handlers', async () => {
  vi.mocked(api.getTechniqueCatalog).mockResolvedValue({ catalogVersion: '1', techniques: [] });
  vi.mocked(api.getTechniqueFavorites).mockResolvedValue({ ids: [] });
  const canvasPointer = vi.fn();
  const canvasClick = vi.fn();
  render(<QueryClientProvider client={new QueryClient()}><div onPointerDown={canvasPointer} onClick={canvasClick}><TechniqueLibrary /></div></QueryClientProvider>);
  const trigger = screen.getByRole('button', { name: '手法库' });
  const user = userEvent.setup();
  await user.pointer([{ target: trigger, keys: '[MouseLeft>]' }, { target: trigger, keys: '[/MouseLeft]' }]);
  expect(await screen.findByRole('dialog')).toBeInTheDocument();
  expect(trigger).toHaveAttribute('aria-expanded', 'true');
  expect(canvasPointer).not.toHaveBeenCalled();
  expect(canvasClick).not.toHaveBeenCalled();
});

it('ignores old responses and aborts an obsolete search', async () => {
  let resolveOld!: (value: Awaited<ReturnType<typeof api.getTechniqueCases>>) => void;
  vi.mocked(api.getTechniqueCases).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
    .mockResolvedValue({ items: [], total: 0, offset: 0, limit: 24 });
  render(<QueryClientProvider client={new QueryClient()}><CaseBrowser username={null} onTechnique={vi.fn()} /></QueryClientProvider>);
  await waitFor(() => expect(resolveOld).toBeDefined());
  const calls = vi.mocked(api.getTechniqueCases).mock.calls;
  const oldSignal = calls[calls.length - 1][1]!;
  await userEvent.setup().type(screen.getByRole('searchbox'), 'new');
  await screen.findByText('没有匹配的案例');
  expect(oldSignal.aborted).toBe(true);
  await act(async () => resolveOld({ items: [{ id: 'stale', title: '过期案例' } as api.TechniqueCase], total: 1, offset: 0, limit: 24 }));
  expect(screen.queryByText('过期案例')).not.toBeInTheDocument();
});

it('browsing cases preserves drafts and linked techniques apply the exact version despite prior filters', async () => {
  const card: TechniqueCard = { id: 'linked', version: '2.3.1', status: 'active', title: '关联手法甲', summary: '摘要', category: '人物反应', intent: '意图', content_hash: 'x', sources: [{ url: 'https://example.com/source', credit: '官方参考', source_type: 'official', checked_at: '', basis: '根据官方说明提炼；未观看视频，未本地实测。' }], use_cases: ['cinema'], case_ids: ['case-one'], applicability: { modes: ['i2v'], min_duration_seconds: 1, max_duration_seconds: 20, last_frame_constraint: 'allowed' } };
  const example: api.TechniqueCase = { id: 'case-one', title: '案例一', summary: '原创概述', use_cases: ['cinema'], provenance: 'official', local_verification: 'unverified', related_technique_ids: [card.id], sources: [{ repository: 'repo', revision: 'sha', path: 'readme', provenance: 'official', url: 'javascript:alert(1)' }] };
  vi.mocked(api.getTechniqueCatalog).mockResolvedValue({ catalogVersion: '1', techniques: [card] });
  vi.mocked(api.getTechniqueFavorites).mockResolvedValue({ ids: [] });
  vi.mocked(api.getTechniqueCases).mockResolvedValue({ items: [example], total: 1, offset: 0, limit: 24 });
  vi.mocked(api.getTechniqueCase).mockResolvedValue(example);
  const onSelect = vi.fn();
  const segment = createDirectorDraft('s1').segments[0];
  const snapshot = JSON.stringify(segment);
  const capabilities = { modes: [], fps: 24, frameStep: 17, frameOffset: 5 } as unknown as DirectorCapabilities;
  render(<QueryClientProvider client={new QueryClient()}><TechniqueLibrary context={{ segment, capabilities, hasReferences: false, onSelect }} /></QueryClientProvider>);
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: '手法库' }));
  await screen.findByRole('button', { name: '查看 关联手法甲' });
  await user.selectOptions(screen.getByLabelText('手法用途'), 'music');
  expect(screen.queryByRole('button', { name: '查看 关联手法甲' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: /^案例$/ }));
  await user.click(await screen.findByRole('button', { name: /案例一/ }));
  await screen.findByText('原创概述');
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
  expect(onSelect).not.toHaveBeenCalled();
  expect(JSON.stringify(segment)).toBe(snapshot);
  await user.click(await screen.findByRole('button', { name: '查看手法 · 关联手法甲' }));
  expect(screen.getByText('根据官方说明提炼；未观看视频，未本地实测。')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '应用到当前分段' }));
  expect(onSelect).toHaveBeenCalledExactlyOnceWith({ id: 'linked', version: '2.3.1' });
});

it('pages on the server, resets filtering to page one and retries errors', async () => {
  vi.mocked(api.getTechniqueCases).mockResolvedValue({ items: [], total: 50, offset: 0, limit: 24 });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><CaseBrowser username={null} onTechnique={vi.fn()} /></QueryClientProvider>);
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: '下一页' }));
  await waitFor(() => expect(api.getTechniqueCases).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 24, limit: 24 }), expect.any(AbortSignal)));
  vi.mocked(api.getTechniqueCases).mockRejectedValueOnce(new Error('offline'));
  await user.selectOptions(screen.getByLabelText('案例用途'), 'music');
  await screen.findByText('案例读取失败');
  expect(api.getTechniqueCases).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0, use_case: 'music' }), expect.any(AbortSignal));
  await user.click(screen.getByRole('button', { name: '重试案例' }));
  await waitFor(() => expect(screen.queryByText('案例读取失败')).not.toBeInTheDocument());
});
