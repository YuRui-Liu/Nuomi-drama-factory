import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ScriptDocument } from '@/features/script-creation/types';
const api = vi.hoisted(() => ({ listConsistencyRuns: vi.fn(), listProposals: vi.fn(), startConsistencyRun: vi.fn(), markIntentional: vi.fn(), acceptProposals: vi.fn(), discardProposal: vi.fn() }));
const workflowApi = vi.hoisted(() => ({ screenplayReview: vi.fn(), reviewScreenplay: vi.fn() }));
vi.mock('@/features/script-creation/api', () => ({ scriptCreationApi: api }));
vi.mock('@/features/canvas/production/creativeWorkflowApi', () => ({ creativeWorkflowApi: workflowApi }));
import { ScreenplayReview } from '@/features/canvas/production/ScreenplayReview';
import { ManualScreenplayReview } from '@/features/canvas/production/ManualScreenplayReview';
const doc = { id: 'script', title: '当前剧本', current_revision_id: 'rev2', revision: { markdown: '😀开场\n剧本证据在这里', id: 'rev2' } } as ScriptDocument;
const ref = { id: 'outline', title: '大纲', current_revision_id: 'outline-r1', revision: { markdown: '前言😀\n大纲证据在这里', id: 'outline-r1' } } as ScriptDocument;
const currentRun = (overrides = {}) => ({ id: 'r', episode_document_id: 'script', mode: 'actual', status: 'completed', context_revisions: { script: 'rev2' }, issues: [], ...overrides });
function mount({ document = doc, references = [], saved = true, rewritePending = false, ...props }: {
  document?: ScriptDocument; references?: ScriptDocument[]; saved?: boolean; rewritePending?: boolean;
  onLocate?: (target: { document_id: string; revision_id: string; offset: number }) => void;
  onCheckReadyChange?: (ready: boolean) => void;
  locatableDocumentIds?: string[];
} = {}) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ScreenplayReview project="p" document={document} references={references} saved={saved} refresh={vi.fn()} rewritePending={rewritePending} {...props} /></QueryClientProvider>);
}
beforeEach(() => { vi.clearAllMocks(); api.listConsistencyRuns.mockResolvedValue([]); api.listProposals.mockResolvedValue([]); });
it('runs a real revision-bound check only on explicit action', async () => {
  api.startConsistencyRun.mockResolvedValue({}); mount();
  expect(api.startConsistencyRun).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('检查当前剧本'));
  await waitFor(() => expect(api.startConsistencyRun).toHaveBeenCalledWith('p', expect.objectContaining({ episode_document_id: 'script', context_revisions: { script: 'rev2' }, proposal_id: null })));
});
it('does not allow an old candidate to overwrite a newer screenplay', async () => {
  api.listProposals.mockResolvedValue([{ id: 'proposal', document_id: 'script', status: 'pending', base_revision_id: 'rev1', before: '旧文', after: '改稿', reason: '压缩' }]); mount();
  await screen.findByText('压缩');
  expect(screen.getByText('查看修改对比')).toBeInTheDocument();
  expect(screen.getByText('查看修改对比').closest('details')).not.toHaveAttribute('open');
  expect(screen.getByRole('button', { name: '采用此修改' })).toBeDisabled();
  expect(api.acceptProposals).not.toHaveBeenCalled();
});
it('requires a stated reason instead of silently waiving fact issues', async () => {
  api.listConsistencyRuns.mockResolvedValue([{ id: 'r', episode_document_id: 'script', mode: 'actual', status: 'completed', context_revisions: { script: 'rev2' }, issues: [{ id: 'issue', category: 'fact', explanation: '道具冲突', stale: false }] }]); mount();
  await screen.findByText(/道具冲突/);
  expect(screen.getByRole('button', { name: '记录理由' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('保留此设定的理由'), { target: { value: '此处是角色的谎言' } });
  fireEvent.click(screen.getByRole('button', { name: '记录理由' }));
  await waitFor(() => expect(api.markIntentional).toHaveBeenCalledWith('p', 'issue', '此处是角色的谎言'));
});

it('marks a screenplay check outdated when any current context revision differs', async () => {
  const onCheckReadyChange = vi.fn();
  api.listConsistencyRuns.mockResolvedValue([currentRun({ context_revisions: { script: 'rev2', outline: 'outline-r0' } })]);
  mount({ references: [ref], onCheckReadyChange });
  expect(await screen.findByText('检查已过期')).toBeInTheDocument();
  await waitFor(() => expect(onCheckReadyChange).toHaveBeenLastCalledWith(false));
});

it('locates only a unique quotation from its exact current revision', async () => {
  const onLocate = vi.fn();
  api.listConsistencyRuns.mockResolvedValue([currentRun({ context_revisions: { script: 'rev2', outline: 'outline-r1' }, issues: [{
    id: 'issue', category: 'creative', explanation: '前后呼应', suggested_action: '保留线索', stale: false,
    source: { document_id: 'outline', revision_id: 'outline-r1', quote: '大纲证据' },
    target: { document_id: 'script', revision_id: 'rev2', quote: '剧本证据' },
  }] })]);
  mount({ references: [ref], onLocate, locatableDocumentIds: ['script', 'outline'] });
  fireEvent.click(await screen.findByRole('button', { name: '定位依据' }));
  expect(onLocate).toHaveBeenLastCalledWith({ document_id: 'outline', revision_id: 'outline-r1', offset: ref.revision.markdown.indexOf('大纲证据') });
  fireEvent.click(screen.getByRole('button', { name: '定位剧本' }));
  expect(onLocate).toHaveBeenLastCalledWith({ document_id: 'script', revision_id: 'rev2', offset: doc.revision.markdown.indexOf('剧本证据') });
});

it('does not locate a repeated or outdated quotation', async () => {
  const onLocate = vi.fn();
  api.listConsistencyRuns.mockResolvedValue([currentRun({ issues: [{
    id: 'issue', category: 'creative', explanation: '线索位置不确定', suggested_action: '', stale: false,
    source: { document_id: 'script', revision_id: 'old-rev', quote: '剧本证据' },
    target: { document_id: 'script', revision_id: 'rev2', quote: '剧本证据' },
  }] })]);
  mount({ document: { ...doc, revision: { ...doc.revision, markdown: `${doc.revision.markdown}\n剧本证据再次出现` } }, onLocate });
  expect(await screen.findByRole('button', { name: '定位依据' })).toBeDisabled();
  expect(screen.getByText('依据版本已变化，无法定位')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '定位剧本' })).toBeDisabled();
  expect(screen.getByText('引文在正文中不唯一，无法定位')).toBeInTheDocument();
  expect(onLocate).not.toHaveBeenCalled();
});

it('keeps reference quotations unavailable when their document is outside the editor navigation scope', async () => {
  const onLocate = vi.fn();
  api.listConsistencyRuns.mockResolvedValue([currentRun({ context_revisions: { script: 'rev2', outline: 'outline-r1' }, issues: [{
    id: 'issue', category: 'creative', explanation: '前后呼应', suggested_action: '', stale: false,
    source: { document_id: 'outline', revision_id: 'outline-r1', quote: '大纲证据' }, target: null,
  }] })]);
  mount({ references: [ref], onLocate, locatableDocumentIds: ['script'] });
  expect(await screen.findByRole('button', { name: '定位依据' })).toBeDisabled();
  expect(screen.getByText('该文档当前未打开，无法定位')).toBeInTheDocument();
  expect(onLocate).not.toHaveBeenCalled();
});

it('disables location until the current screenplay edits are saved', async () => {
  const onLocate = vi.fn();
  api.listConsistencyRuns.mockResolvedValue([currentRun({ issues: [{
    id: 'issue', category: 'creative', explanation: '台词节奏', suggested_action: '', stale: false,
    source: null, target: { document_id: 'script', revision_id: 'rev2', quote: '剧本证据' },
  }] })]);
  mount({ saved: false, onLocate });
  expect(await screen.findByRole('button', { name: '定位剧本' })).toBeDisabled();
  expect(screen.getByText('请先保存当前编辑后定位')).toBeInTheDocument();
  expect(onLocate).not.toHaveBeenCalled();
});

it('reports handoff readiness only after current fact issues are resolved', async () => {
  const onCheckReadyChange = vi.fn();
  const issue = { id: 'fact', category: 'fact', explanation: '关键事实冲突', suggested_action: '', stale: false,
    source: null, target: null, proposal_id: null, intentional_reason: null };
  api.listConsistencyRuns.mockResolvedValue([currentRun({ context_revisions: { script: 'rev2' }, issues: [issue] })]);
  mount({ onCheckReadyChange });
  await screen.findByText('关键事实冲突');
  expect(await screen.findByText('有待处理的事实问题')).toBeInTheDocument();
  expect(onCheckReadyChange).toHaveBeenLastCalledWith(false);
});

it('keeps completed, current consistency readiness when all fact issues are addressed', async () => {
  const onCheckReadyChange = vi.fn();
  api.listConsistencyRuns.mockResolvedValue([currentRun({ context_revisions: { script: 'rev2' }, issues: [{
    id: 'fact', category: 'fact', explanation: '关键事实冲突', suggested_action: '', stale: false,
    source: null, target: null, proposal_id: null, intentional_reason: '这是角色故意撒谎',
  }] })]);
  mount({ onCheckReadyChange });
  expect(await screen.findByText('已完成当前版本检查')).toBeInTheDocument();
  await waitFor(() => expect(onCheckReadyChange).toHaveBeenLastCalledWith(true));
});

it('shows independent review issues directly and collapses its summary', async () => {
  const onLocate = vi.fn();
  workflowApi.screenplayReview.mockResolvedValue({ status: 'needs_revision', result: {
    summary: '转折缺少铺垫', coverage: ['人物动机'], issues: [{ id: 'i', severity: 'blocker', quote: '剧本证据', reason: '转折来得太快', repair_goal: '补一处铺垫' }],
    strengths: [{ quote: '人物对峙', reason: '冲突鲜明' }],
  } });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ManualScreenplayReview project="p" documentId="script" revisionId="rev2" planRunId="plan" saved markdown={doc.revision.markdown} markdownRevisionId="rev2" onLocate={onLocate} /></QueryClientProvider>);
  expect(await screen.findByText(/转折来得太快/)).toBeInTheDocument();
  expect(screen.getByText('完整审稿摘要与优点')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '定位原文' }));
  expect(onLocate).toHaveBeenCalledWith({ document_id: 'script', revision_id: 'rev2', offset: doc.revision.markdown.indexOf('剧本证据') });
});

it('does not locate a manual review issue inside a mismatched markdown revision', async () => {
  const onLocate = vi.fn();
  workflowApi.screenplayReview.mockResolvedValue({ status: 'needs_revision', result: {
    issues: [{ id: 'i', severity: 'blocker', quote: '剧本证据', reason: '需要铺垫', repair_goal: '增加铺垫' }],
  } });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ManualScreenplayReview project="p" documentId="script" revisionId="rev2" planRunId="plan" saved markdown={doc.revision.markdown} markdownRevisionId="rev1" onLocate={onLocate} /></QueryClientProvider>);
  expect(await screen.findByRole('button', { name: '定位原文' })).toBeDisabled();
  expect(screen.getByText('正文版本与审稿结果不一致，无法定位')).toBeInTheDocument();
  expect(onLocate).not.toHaveBeenCalled();
});
