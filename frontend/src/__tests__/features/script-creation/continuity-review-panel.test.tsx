import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ContinuityReviewPanel } from '@/features/script-creation/continuity-review-panel';
import { longformStoryApi, type ContinuityContext, type ContinuityFact } from '@/api/longform-story';

vi.mock('@/api/longform-story', () => ({ longformStoryApi: { context: vi.fn(), listFacts: vi.fn(), adoptFact: vi.fn() } }));
const fact: ContinuityFact = { id: 'f1', subject_id: '林夏', predicate: '发现', object_id: '钥匙', state: 'occurred', adoption_status: 'candidate', episode_start: 5, episode_end: 5, source_document_id: 'script5', source_revision_id: 'r17', source_locator: 'block-2' };
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(longformStoryApi.context).mockResolvedValue({ episode_number: 5, facts: [] });
  vi.mocked(longformStoryApi.listFacts).mockResolvedValue([fact]);
});
afterEach(cleanup);

it.each([1, 2, 3])('includes episode %s in the inclusive E1–E3 fact range', async episode => {
  const ranged = { ...fact, adoption_status: 'adopted', episode_start: 1, episode_end: 3 };
  vi.mocked(longformStoryApi.context).mockResolvedValue({ episode_number: episode, facts: [ranged] });
  vi.mocked(longformStoryApi.listFacts).mockResolvedValue([ranged]);
  render(<ContinuityReviewPanel project="p" documentId="outline" episode={episode} />);
  expect(await screen.findByText('已采用 · 连续性上下文')).toBeVisible();
  expect(screen.getByText('已采用')).toBeVisible();
});

it('keeps retracted records visible separately even outside their former range', async () => {
  const retracted = { ...fact, state: 'retracted', adoption_status: 'adopted', episode_start: 1, episode_end: 3 };
  vi.mocked(longformStoryApi.context).mockResolvedValue({ episode_number: 4, facts: [retracted] });
  vi.mocked(longformStoryApi.listFacts).mockResolvedValue([retracted]);
  render(<ContinuityReviewPanel project="p" documentId="outline" episode={4} />);
  const records = await screen.findByLabelText('已撤回连续性记录');
  expect(within(records).getByText('已撤回 · 不用于当前创作')).toBeVisible();
  expect(within(records).getByText(/script5 · r17 · block-2/)).toBeVisible();
  expect(screen.queryByText('已采用 · 连续性上下文')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '审阅并采用' })).not.toBeInTheDocument();
  expect(within(records).getAllByText(/林夏/)).toHaveLength(1);
});

it('does not present out-of-range or retracted candidates as active facts', async () => {
  const expired = { ...fact, id: 'expired', episode_start: 1, episode_end: 3, adoption_status: 'adopted' };
  const retracted = { ...fact, id: 'retracted', state: 'retracted' };
  vi.mocked(longformStoryApi.context).mockResolvedValue({ episode_number: 5, facts: [expired] });
  vi.mocked(longformStoryApi.listFacts).mockResolvedValue([expired, retracted]);
  render(<ContinuityReviewPanel project="p" documentId="outline" episode={5} />);
  expect(await screen.findByLabelText('已撤回连续性记录')).toBeVisible();
  expect(screen.queryByText('已采用 · 连续性上下文')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '审阅并采用' })).not.toBeInTheDocument();
});

it('keeps a persisted candidate out of the authoritative context until explicit adoption succeeds', async () => {
  const adopted = { ...fact, adoption_status: 'adopted' };
  render(<ContinuityReviewPanel project="p" documentId="outline" episode={5} />);
  expect(await screen.findByText('候选 · 尚未采用')).toBeVisible();
  expect(screen.getByText(/script5 · r17 · block-2/)).toBeVisible();
  expect(within(screen.getByLabelText('已采用连续性事实')).queryByText(/林夏/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '审阅并采用' }));
  expect(longformStoryApi.adoptFact).not.toHaveBeenCalled();
  let resolve!: (value: ContinuityFact) => void;
  vi.mocked(longformStoryApi.adoptFact).mockReturnValue(new Promise(done => { resolve = done; }));
  fireEvent.click(screen.getByRole('button', { name: '确认采用' }));
  expect(screen.getByText('候选 · 尚未采用')).toBeVisible();
  vi.mocked(longformStoryApi.listFacts).mockResolvedValue([adopted]);
  vi.mocked(longformStoryApi.context).mockResolvedValue({ episode_number: 5, facts: [adopted] });
  await act(async () => resolve(adopted));
  expect(await screen.findByText('已采用 · 连续性上下文')).toBeVisible();
  expect(screen.queryByRole('button', { name: '审阅并采用' })).not.toBeInTheDocument();
});

it('preserves unknown adoption and retries the same mutation, without marking it adopted', async () => {
  vi.mocked(longformStoryApi.adoptFact).mockRejectedValue(new Error('网络中断'));
  render(<ContinuityReviewPanel project="p" documentId="outline" episode={5} />);
  fireEvent.click(await screen.findByRole('button', { name: '审阅并采用' }));
  fireEvent.click(screen.getByRole('button', { name: '确认采用' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('结果尚未确认');
  expect(screen.getByText('候选 · 尚未采用')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '确认采用' }));
  await waitFor(() => expect(longformStoryApi.adoptFact).toHaveBeenCalledTimes(2));
  expect(vi.mocked(longformStoryApi.adoptFact).mock.calls[0]).toEqual(vi.mocked(longformStoryApi.adoptFact).mock.calls[1]);
});

it('ignores late episode previews and does not label read errors as an empty fact set', async () => {
  let resolve!: (value: ContinuityContext) => void;
  vi.mocked(longformStoryApi.context).mockReturnValueOnce(new Promise(done => { resolve = done; }));
  const { rerender } = render(<ContinuityReviewPanel project="p" documentId="outline" episode={5} />);
  vi.mocked(longformStoryApi.context).mockRejectedValueOnce(new Error('没有读取权限'));
  rerender(<ContinuityReviewPanel project="p" documentId="outline" episode={100} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('没有读取权限');
  await act(async () => resolve({ episode_number: 5, facts: [{ ...fact, adoption_status: 'adopted' }] }));
  expect(screen.queryByText('已采用 · 连续性上下文')).not.toBeInTheDocument();
  expect(screen.queryByText('截至本集暂无可用的已采用连续性事实。')).not.toBeInTheDocument();
});
