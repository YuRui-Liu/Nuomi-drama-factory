import { render, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { DocumentEditor } from '@/features/script-creation/document-editor';
import { DraftManager } from '@/features/script-creation/draft-manager';
import type { ScriptDocument } from '@/features/script-creation/types';

it('locates a quotation inside a paragraph without entering edit mode and ignores stale revisions', async () => {
  const markdown = '# 场景\n\n门外很安静。撞门声突然响起。';
  const document = { id: 'doc', title: '剧本', kind: 'episode_script', current_revision_id: 'r1',
    revision: { markdown, blocks: [] } } as unknown as ScriptDocument;
  const manager = new DraftManager(vi.fn());
  manager.load(document);
  const props = { project: 'p', manager, draft: manager.get('doc')!, onSelection: vi.fn() };
  const view = render(<DocumentEditor {...props} readingTarget={{ document_id: 'doc', revision_id: 'r1', offset: markdown.indexOf('撞门声') }} />);
  await waitFor(() => expect(view.container.querySelector('#document-editor-reading-target')).toHaveTextContent('撞门声'));
  expect(view.container.querySelector('textarea')).toBeNull();
  view.rerender(<DocumentEditor {...props} readingTarget={{ document_id: 'doc', revision_id: 'old', offset: markdown.indexOf('撞门声') }} />);
  expect(view.container.querySelector('#document-editor-reading-target')).toBeNull();
  manager.dispose();
});
