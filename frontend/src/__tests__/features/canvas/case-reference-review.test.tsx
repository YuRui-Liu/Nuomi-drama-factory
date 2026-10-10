import { expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { CaseReferenceReview } from '@/features/technique-library/CaseReferenceReview';
import * as api from '@/api/caseReference';
import { createDirectorDraft } from '@/features/canvas/domain/videoDirectorDraft';
import { draftToWire } from '@/api/videoDirector';
import type { TechniqueCase } from '@/api/techniqueLibrary';
vi.mock('@/api/caseReference', () => ({ listCaseProposals: vi.fn(), createCaseProposal: vi.fn(), getCaseProposal: vi.fn(), cancelCaseProposal: vi.fn() }));

const item: TechniqueCase = { id: 'case', title: '案例', summary: '概要', sources: [], use_cases: [], provenance: 'author', local_verification: 'unverified', related_technique_ids: [] };
function setup(status?: api.CaseProposal['status'], onApply = vi.fn().mockResolvedValue(undefined)) {
  const draft = createDirectorDraft('s1'); draft.segments[0].prompt = '林澈走出教室。';
  const proposal = { id: 'p1', status: status ?? 'queued', input_fingerprint: 'fixture-fingerprint', input: { case_id: 'case', segment_id: 's1', canvas_id: 'canvas', node_id: 'node', draft: draftToWire(draft), effective_draft: draftToWire(draft), dimensions: ['camera'] }, evidence: { prompts: [], description: '', media_analyzed: false },
    result: status === 'succeeded' ? { prompt: '缓慢跟拍林澈走出教室。', compatible: true, conflict: null, suggestions: [{ dimension: 'camera', suggestion: '慢跟拍', basis: '原文' }] } : null } as api.CaseProposal;
  vi.mocked(api.listCaseProposals).mockResolvedValue(status ? [proposal] : []);
  vi.mocked(api.getCaseProposal).mockResolvedValue(proposal);
  const context = { project: 'project', canvasId: 'canvas', nodeId: 'node', segmentId: 's1', draft, effectiveDraft: draft, onApply };
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><CaseReferenceReview context={context} item={item} username="alice" /></QueryClientProvider>);
  return { context, proposal, onApply };
}

it('requires concrete evidence and selected dimensions before submitting', async () => {
  const { context } = setup();
  const user = userEvent.setup();
  expect(screen.getByRole('button', { name: '生成参考方案' })).toBeDisabled();
  await user.type(screen.getByLabelText('补充案例画面描述（可选）'), '镜头缓慢跟拍至教室门口后停止。');
  expect(screen.getByRole('button', { name: '生成参考方案' })).toBeEnabled();
  vi.mocked(api.createCaseProposal).mockResolvedValue({ id: 'new', status: 'queued' } as api.CaseProposal);
  await user.click(screen.getByRole('button', { name: '生成参考方案' }));
  await waitFor(() => expect(api.createCaseProposal).toHaveBeenCalledWith(context, 'case', expect.any(String), ['camera'], [], '镜头缓慢跟拍至教室门口后停止。'));
});

it('edits and explicitly applies a fresh server proposal', async () => {
  const { onApply, proposal } = setup('succeeded');
  const user = userEvent.setup();
  const editor = await screen.findByLabelText('建议提示词');
  await user.clear(editor); await user.type(editor, '修改后的镜头。');
  expect(onApply).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: '确认应用到当前分段' }));
  await waitFor(() => expect(onApply).toHaveBeenCalledWith(proposal, '修改后的镜头。'));
  expect(await screen.findByText('已应用到画布草稿；保存状态请查看画布提示。')).toBeInTheDocument();
});

it('retains the proposal and shows stale/application errors without a success message', async () => {
  setup('succeeded', vi.fn().mockRejectedValue(new Error('方案已过期')));
  await userEvent.setup().click(await screen.findByRole('button', { name: '确认应用到当前分段' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('方案已过期');
  expect(screen.getByLabelText('建议提示词')).toHaveValue('缓慢跟拍林澈走出教室。');
  expect(screen.queryByText(/已应用到画布草稿/)).not.toBeInTheDocument();
});

it('cancels an active proposal without applying it', async () => {
  const { onApply } = setup('running');
  vi.mocked(api.cancelCaseProposal).mockResolvedValue({ id: 'p1', status: 'cancelled' } as api.CaseProposal);
  await userEvent.setup().click(await screen.findByRole('button', { name: '取消方案' }));
  await waitFor(() => expect(api.cancelCaseProposal).toHaveBeenCalledWith('project', 'p1'));
  expect(onApply).not.toHaveBeenCalled();
});
