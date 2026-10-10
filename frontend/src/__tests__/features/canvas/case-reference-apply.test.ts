import { expect, it, vi } from 'vitest';
import { webcrypto } from 'node:crypto';
import { applyCaseReference } from '@/features/canvas/director/applyCaseReference';
import { createDirectorDraft, addSegment } from '@/features/canvas/domain/videoDirectorDraft';
import { draftToWire, draftFromWire } from '@/api/videoDirector';
import type { CaseProposal } from '@/api/caseReference';

function fixture() {
  const draft = addSegment(createDirectorDraft('s1'), 's2');
  draft.segments[0].prompt = '林澈走出教室。';
  const proposal: CaseProposal = { id: 'p1', status: 'succeeded', input_fingerprint: 'fingerprint',
    input: { case_id: 'case', canvas_id: 'canvas', node_id: 'node', segment_id: 's1', dimensions: ['camera'],
      draft: draftToWire(draft), effective_draft: draftToWire(draft) },
    evidence: { prompts: [], description: '跟拍至门口', media_analyzed: false },
    result: { prompt: '缓慢跟拍林澈走出教室。', suggestions: [], compatible: true, conflict: null } };
  return { draft, proposal };
}

it('updates only target prompt and provenance and survives wire serialization', async () => {
  vi.stubGlobal('crypto', webcrypto);
  const { draft, proposal } = fixture();
  const changed = await applyCaseReference(draft, draft, proposal, '编辑后的跟拍林澈走出教室。');
  expect(changed.revision).toBe(draft.revision + 1);
  expect(changed.segments[1]).toEqual(draft.segments[1]);
  expect(changed.segments[0]).toMatchObject({ ...draft.segments[0], prompt: '编辑后的跟拍林澈走出教室。' });
  expect(draftFromWire(draftToWire(changed)).segments[0].caseReference).toEqual(changed.segments[0].caseReference);
  expect(draft.segments[0].caseReference).toBeUndefined();
});

it('rejects revision changes, deleted target, changed references and cancelled proposals', async () => {
  const { draft, proposal } = fixture();
  await expect(applyCaseReference({ ...draft, revision: 99 }, draft, proposal, '新')).rejects.toThrow('过期');
  await expect(applyCaseReference({ ...draft, segments: [] }, draft, proposal, '新')).rejects.toThrow('过期');
  await expect(applyCaseReference(draft, { ...draft, references: [{ imageId: 'new', url: '/new.png' }] }, proposal, '新')).rejects.toThrow('过期');
  await expect(applyCaseReference(draft, draft, { ...proposal, status: 'cancelled' }, '新')).rejects.toThrow('取消');
});
