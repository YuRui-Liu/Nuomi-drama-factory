import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { LongformOutlinePanel } from '@/features/script-creation/longform-outline-panel';
import { longformStoryApi, type LongformDocument } from '@/api/longform-story';

vi.mock('@/api/longform-story', () => ({ longformStoryApi: { getOutline: vi.fn(), saveOutline: vi.fn(), context: vi.fn(), listFacts: vi.fn(), adoptFact: vi.fn() } }));
const document = (id = 'outline', revision = 'r1'): LongformDocument => ({
  id, kind: 'outline', title: '百集故事', current_revision_id: revision, adopted_revision_id: null,
  episode_number: null, source_origin: null, created_at: '', updated_at: '',
  revision: { id: revision, document_id: id, parent_revision_id: null, markdown: '', blocks: [], client_mutation_id: '', created_at: '', restored_from_revision_id: null,
    outline_items: [
      { id: 'series', parent_id: null, ordinal: 1, kind: 'series_arc', title: '寻找真相', episode_start: 1, episode_end: 100 },
      { id: 'arc', parent_id: 'series', ordinal: 1, kind: 'story_arc', title: '终局', episode_start: 91, episode_end: 100 },
      { id: 'ep1', parent_id: 'series', ordinal: 2, kind: 'episode', title: '开场', episode_start: 1, episode_end: 1 },
      { id: 'ep100', parent_id: 'arc', ordinal: 1, kind: 'episode', title: '真相大白', episode_start: 100, episode_end: 100, source_document_id: 'script100', source_revision_id: 'source-r7', source_locator: 'scene-3' },
    ] },
});
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(longformStoryApi.getOutline).mockResolvedValue(document());
  vi.mocked(longformStoryApi.context).mockResolvedValue({ episode_number: 1, facts: [] });
  vi.mocked(longformStoryApi.listFacts).mockResolvedValue([]);
});
afterEach(cleanup);

it('filters episode 100 with its ancestors and preserves source versions', async () => {
  render(<LongformOutlinePanel project="p" documentId="outline" />);
  await screen.findByDisplayValue('真相大白');
  fireEvent.change(screen.getByLabelText('查看集数'), { target: { value: '100' } });
  await waitFor(() => expect(longformStoryApi.context).toHaveBeenLastCalledWith('p', 100));
  expect(screen.getByLabelText('全剧主线标题 series')).toHaveValue('寻找真相');
  expect(screen.getByLabelText('故事弧标题 arc')).toHaveValue('终局');
  expect(screen.queryByDisplayValue('开场')).not.toBeInTheDocument();
  expect(screen.getByText(/source-r7/)).toBeVisible();
  expect(screen.getByText('当前版本未采用')).toBeVisible();
});

it('preserves edits and retries the identical versioned mutation after a failed save', async () => {
  vi.mocked(longformStoryApi.saveOutline).mockRejectedValueOnce(new Error('版本冲突'));
  const saved = document('outline', 'r2'); saved.revision.outline_items[3].title = '新结尾';
  vi.mocked(longformStoryApi.saveOutline).mockResolvedValueOnce(saved);
  render(<LongformOutlinePanel project="p" documentId="outline" />);
  fireEvent.change(await screen.findByDisplayValue('真相大白'), { target: { value: '新结尾' } });
  fireEvent.click(screen.getByRole('button', { name: '保存大纲' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('版本冲突');
  expect(screen.getByDisplayValue('新结尾')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '保存大纲' }));
  await waitFor(() => expect(screen.getByText('当前版本：r2')).toBeVisible());
  const calls = vi.mocked(longformStoryApi.saveOutline).mock.calls;
  expect(calls[0]).toEqual(calls[1]);
  expect(calls[0][2]).toMatchObject({ base_revision_id: 'r1', client_mutation_id: expect.any(String) });
});

it('does not let a late document load replace another document scope', async () => {
  let resolve!: (doc: LongformDocument) => void;
  vi.mocked(longformStoryApi.getOutline).mockReturnValueOnce(new Promise(done => { resolve = done; }));
  const { rerender } = render(<LongformOutlinePanel project="p" documentId="old" />);
  rerender(<LongformOutlinePanel project="p" documentId="new" />);
  await screen.findByDisplayValue('真相大白');
  await act(async () => resolve({ ...document('old'), title: '旧作用域' }));
  expect(screen.queryByText('旧作用域')).not.toBeInTheDocument();
});

it('edits a 100-episode outline without truncating the saved hierarchy or permitting cyclic parents', async () => {
  const full = document();
  full.revision.outline_items = [full.revision.outline_items[0], ...Array.from({ length: 100 }, (_, index) => ({
    id: `episode-${index + 1}`, parent_id: 'series', ordinal: index + 1, kind: 'episode' as const, title: `第${index + 1}集`, episode_start: index + 1, episode_end: index + 1,
  }))];
  vi.mocked(longformStoryApi.getOutline).mockResolvedValue(full);
  vi.mocked(longformStoryApi.saveOutline).mockResolvedValue(full);
  render(<LongformOutlinePanel project="p" documentId="outline" />);
  await screen.findByLabelText('分集标题 episode-100');
  expect(screen.getAllByRole('textbox')).toHaveLength(101);
  expect(screen.getByLabelText('上级 series').querySelectorAll('option')).toHaveLength(1);
  fireEvent.change(screen.getByLabelText('查看集数'), { target: { value: '100' } });
  fireEvent.change(screen.getByLabelText('分集标题 episode-100'), { target: { value: '百集终章' } });
  fireEvent.click(screen.getByRole('button', { name: '保存大纲' }));
  await waitFor(() => expect(longformStoryApi.saveOutline).toHaveBeenCalledTimes(1));
  const saved = vi.mocked(longformStoryApi.saveOutline).mock.calls[0][2].items;
  expect(saved).toHaveLength(101);
  expect(saved[100]).toMatchObject({ id: 'episode-100', ordinal: 100, title: '百集终章' });
  expect(saved[1]).toMatchObject({ id: 'episode-1', title: '第1集' });
});

it('preserves failed drafts until explicitly discarded, then reads the latest server version', async () => {
  vi.mocked(longformStoryApi.saveOutline).mockRejectedValue(new Error('版本冲突'));
  render(<LongformOutlinePanel project="p" documentId="outline" />);
  fireEvent.change(await screen.findByLabelText('分集标题 ep100'), { target: { value: '本地冲突草稿' } });
  fireEvent.click(screen.getByRole('button', { name: '保存大纲' }));
  await screen.findByRole('alert');
  const latest = document('outline', 'remote-r2'); latest.revision.outline_items[3].title = '服务器新结尾';
  vi.mocked(longformStoryApi.getOutline).mockResolvedValue(latest);
  expect(screen.getByDisplayValue('本地冲突草稿')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '放弃本地修改并读取最新版本' }));
  expect(await screen.findByDisplayValue('服务器新结尾')).toBeVisible();
  expect(screen.getByText('当前版本：remote-r2')).toBeVisible();
  expect(screen.queryByDisplayValue('本地冲突草稿')).not.toBeInTheDocument();
});
