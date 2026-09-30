import { readFileSync } from 'node:fs';
import i18next from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { VideoDirectorPanel } from '@/features/canvas/director/VideoDirectorPanel';
import { createDirectorDraft, addSegment } from '@/features/canvas/domain/videoDirectorDraft';
import { CANVAS_NODE_TYPES, type VideoDirectorNodeData } from '@/features/canvas/domain/canvasNodes';
import type { useVideoDirectorTask } from '@/features/canvas/director/useVideoDirectorTask';
import { useCanvasStore } from '@/stores/canvasStore';

vi.mock('@/features/canvas/ui/AssetLibraryModal', () => ({ AssetLibraryModal: () => null }));
const zhI18n = i18next.createInstance();
await zhI18n.use(initReactI18next).init({ lng: 'zh', fallbackLng: 'zh',
  resources: { zh: { translation: JSON.parse(readFileSync('public/locales/zh/translation.json', 'utf8')) } } });

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
  it('shows the connected first frame as read-only and identifies the saved disconnect fallback', () => {
    const data = makeData();
    data.activeInputMode = 'frames';
    data.draft.segments[0].firstFrame = { imageId: 'manual', url: '/manual.png' };
    useCanvasStore.getState().setCanvasData([
      { id: 'director', type: CANVAS_NODE_TYPES.videoDirector, position: { x: 0, y: 0 }, data },
      { id: 'source', type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl: '/linked.png' } },
    ], [{ id: 'link', source: 'source', target: 'director', data: { edgeKind: 'videoDirectorImage',
      slot: { kind: 'firstFrame', segmentId: 'one' } } }]);
    render(<I18nextProvider i18n={zhI18n}><VideoDirectorPanel nodeId="director" data={data} task={task()}
      onDraftChange={vi.fn()} onClose={vi.fn()} /></I18nextProvider>);
    const first = screen.getByRole('group', { name: '首帧' });
    expect(within(first).getByRole('img', { name: '连线首帧' })).toHaveAttribute('src', expect.stringContaining('/linked.png'));
    expect(within(first).queryByRole('button')).not.toBeInTheDocument();
    expect(within(first).getByText('断线后恢复已选图片')).toBeInTheDocument();
    expect(within(first).getByRole('img', { name: '断线后恢复已选图片' })).toHaveAttribute('src', '/manual.png');
    act(() => useCanvasStore.setState({ edges: [] }));
    const manual = screen.getByRole('group', { name: '首帧' });
    expect(within(manual).getByRole('button', { name: '移除' })).toBeInTheDocument();
    expect(within(manual).getByRole('img')).toHaveAttribute('src', '/manual.png');
  });
  it('delegates generation after two-segment draft edits without passing a stale draft', () => {
    const data = makeData();
    data.draft.references = [{ imageId: 'reference', url: '/reference.png' }];
    data.draft = addSegment(data.draft, 'two');
    const onDraftChange = vi.fn((next) => { data.draft = next; rerender(<VideoDirectorPanel nodeId="n1" data={{ ...data }} task={controller} onDraftChange={onDraftChange} onClose={vi.fn()} />); });
    const controller = task();
    const { rerender } = render(<VideoDirectorPanel nodeId="n1" data={data} task={controller} onDraftChange={onDraftChange} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '生成视频' }));
    expect(controller.generate).toHaveBeenCalledTimes(1);
    expect(controller.generate).toHaveBeenCalledWith();
    const prompts = screen.getAllByRole('textbox');
    fireEvent.change(prompts[0], { target: { value: 'Opening shot' } });
    fireEvent.change(prompts[1], { target: { value: 'Closing shot' } });
    fireEvent.click(screen.getByRole('button', { name: '生成视频' }));
    expect(data.draft.segments.map((segment) => segment.prompt)).toEqual(['Opening shot', 'Closing shot']);
    expect(controller.generate).toHaveBeenCalledTimes(2);
    expect(controller.generate).toHaveBeenLastCalledWith();
    expect(screen.queryByRole('button', { name: /批准|审核|确认优化/ })).not.toBeInTheDocument();
  });

  it('keeps a retired model visible while delegating validation to the task', () => {
    const data = makeData();
    data.draft.modelId = 'retired';
    data.draft.references = [{ imageId: 'r', url: '/r.png' }];
    data.draft.segments[0].prompt = 'Shot';
    const controller = task();
    render(<VideoDirectorPanel nodeId="n2" data={data} task={controller} onDraftChange={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByRole('option', { name: 'retired（不可用）' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '生成视频' }));
    expect(controller.generate).toHaveBeenCalledWith();
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

  it('Escape closes the image picker first and keeps the editor mounted', () => {
    const onClose = vi.fn();
    render(<VideoDirectorPanel nodeId="n4" data={makeData()} task={task()} onDraftChange={vi.fn()} onClose={onClose} />);
    fireEvent.click(screen.getByRole('button', { name: '选择参考图' }));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole('textbox')).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
