import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ScriptDocument, ScriptSceneIdentity } from '@/features/script-creation/types';
import { ScreenplaySceneStructure, type PendingStructure } from '@/features/canvas/production/ScreenplaySceneStructure';

const identities: ScriptSceneIdentity[] = [
  { id: 'scene-a', ordinal: 1, heading: '1-1 门厅 夜', parent_ids: [], anchor_block_ids: [] },
  { id: 'scene-b', ordinal: 2, heading: '1-2 楼道 夜', parent_ids: [], anchor_block_ids: [] },
];
const markdown = '1-1 门厅 夜\n第一场内容\n\n1-2 楼道 夜\n第二场内容';

function document(revisionId: string, text = markdown): ScriptDocument {
  return {
    id: 'script-1', kind: 'episode_script', title: 'E01', episode_number: 1,
    current_revision_id: revisionId, adopted_revision_id: revisionId, source_origin: null,
    created_at: '', updated_at: '',
    revision: { id: revisionId, document_id: 'script-1', parent_revision_id: null, markdown: text,
      blocks: [], client_mutation_id: '', created_at: '', restored_from_revision_id: null,
      scene_identities: identities },
  };
}

function renderStructure(onApply = vi.fn(async (_pending: PendingStructure) => undefined)) {
  const view = render(<ScreenplaySceneStructure document={document('rev-1')} revisionId="rev-1"
    markdown={markdown} saved busy={false} selection={null} history={[]} onApply={onApply} />);
  return { ...view, onApply };
}

it('submits a structure preview against the revision it was built from', async () => {
  const { onApply } = renderStructure();
  fireEvent.click(screen.getAllByRole('button', { name: '上移' })[1]);
  fireEvent.click(screen.getByRole('button', { name: '确认保存结构变更' }));

  await waitFor(() => expect(onApply).toHaveBeenCalledWith(expect.objectContaining({
    change: expect.objectContaining({ sourceRevisionId: 'rev-1' }),
  })));
});

it('discards the preview when the screenplay revision changes', async () => {
  const { rerender, onApply } = renderStructure();
  fireEvent.click(screen.getAllByRole('button', { name: '上移' })[1]);
  expect(screen.getByRole('region', { name: '场次结构变更预览' })).toBeInTheDocument();

  rerender(<ScreenplaySceneStructure document={document('rev-2', markdown.replace('第二场内容', '新保存的第二场内容'))}
    revisionId="rev-2" markdown={markdown.replace('第二场内容', '新保存的第二场内容')}
    saved busy={false} selection={null} history={[]} onApply={onApply} />);

  await waitFor(() => expect(screen.queryByRole('region', { name: '场次结构变更预览' })).not.toBeInTheDocument());
  expect(onApply).not.toHaveBeenCalled();
});
