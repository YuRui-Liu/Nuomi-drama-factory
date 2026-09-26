import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { VideoDirectorPanel } from '@/features/canvas/director/VideoDirectorPanel';
import { createDirectorDraft, addSegment } from '@/features/canvas/domain/videoDirectorDraft';
import type { VideoDirectorNodeData } from '@/features/canvas/domain/canvasNodes';
import type { useVideoDirectorTask } from '@/features/canvas/director/useVideoDirectorTask';

vi.mock('@/features/canvas/ui/AssetLibraryModal', () => ({ AssetLibraryModal: () => null }));

const capabilities = { models: [{ id: 'minimax-h3', label: 'MiniMax H3', adapter: 'h3', referenceAdapter: 'h3_ref' }],
  referenceLimit: 5, effectiveReferenceLimit: 5, configuredReferenceLimit: 5, fps: 24, frameStep: 17, frameOffset: 5,
  params: { resolution: ['720p', '1080p'], aspectRatio: ['9:16', '16:9'] }, sizes: [], modes: [] };

function makeData(): VideoDirectorNodeData {
  return { draft: createDirectorDraft('one'), activeAttemptId: null, videoUrl: null, resultRevision: null };
}
function task(overrides: Partial<ReturnType<typeof useVideoDirectorTask>> = {}): ReturnType<typeof useVideoDirectorTask> {
  return { capabilities, attempts: [], error: '', fieldErrors: {}, generate: vi.fn(), recoverPending: vi.fn(),
    retry: vi.fn(), refresh: vi.fn(), ...overrides };
}

describe('video director panel', () => {
  it('requires a valid two-segment reference-only draft before generation', () => {
    const data = makeData();
    data.draft.references = [{ imageId: 'reference', url: '/reference.png' }];
    data.draft = addSegment(data.draft, 'two');
    const onDraftChange = vi.fn((next) => { data.draft = next; rerender(<VideoDirectorPanel nodeId="n1" data={{ ...data }} task={controller} onDraftChange={onDraftChange} onClose={vi.fn()} />); });
    const controller = task();
    const { rerender } = render(<VideoDirectorPanel nodeId="n1" data={data} task={controller} onDraftChange={onDraftChange} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '生成视频' }));
    expect(controller.generate).not.toHaveBeenCalled();
    expect(screen.getAllByText('请输入分段提示词')).toHaveLength(2);
    const prompts = screen.getAllByRole('textbox');
    fireEvent.change(prompts[0], { target: { value: 'Opening shot' } });
    fireEvent.change(prompts[1], { target: { value: 'Closing shot' } });
    fireEvent.click(screen.getByRole('button', { name: '生成视频' }));
    expect(controller.generate).toHaveBeenCalledWith(expect.objectContaining({ segments: [
      expect.objectContaining({ prompt: 'Opening shot' }), expect.objectContaining({ prompt: 'Closing shot' }),
    ] }));
    expect(screen.queryByRole('button', { name: /批准|审核|确认优化/ })).not.toBeInTheDocument();
  });

  it('keeps a retired model visible and blocks generation until changed', () => {
    const data = makeData();
    data.draft.modelId = 'retired';
    data.draft.references = [{ imageId: 'r', url: '/r.png' }];
    data.draft.segments[0].prompt = 'Shot';
    const controller = task();
    render(<VideoDirectorPanel nodeId="n2" data={data} task={controller} onDraftChange={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByRole('option', { name: 'retired（不可用）' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '生成视频' }));
    expect(controller.generate).not.toHaveBeenCalled();
    expect(screen.getByText('当前模型不可用，请选择可用模型')).toBeInTheDocument();
  });

  it('keeps draft edits after the expanded panel closes and reopens', () => {
    const data = makeData();
    const controller = task();
    const onDraftChange = vi.fn((next) => { data.draft = next; view.rerender(<VideoDirectorPanel nodeId="n3" data={{ ...data }} task={controller} onDraftChange={onDraftChange} onClose={vi.fn()} />); });
    const view = render(<VideoDirectorPanel nodeId="n3" data={data} task={controller} onDraftChange={onDraftChange} onClose={vi.fn()} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Persistent shot' } });
    view.unmount();
    render(<VideoDirectorPanel nodeId="n3" data={data} task={controller} onDraftChange={onDraftChange} onClose={vi.fn()} />);
    expect(screen.getByRole('textbox')).toHaveValue('Persistent shot');
  });
});
