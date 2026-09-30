import { readFileSync } from 'node:fs';
import i18next from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { VideoDirectorNode } from '@/features/canvas/nodes/VideoDirectorNode';
import { createDirectorDraft } from '@/features/canvas/domain/videoDirectorDraft';
import type { DirectorAttempt, VideoDirectorNodeData } from '@/features/canvas/domain/canvasNodes';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';

const useTask = vi.hoisted(() => vi.fn());
vi.mock('@/features/canvas/director/useVideoDirectorTask', () => ({ useVideoDirectorTask: useTask }));
vi.mock('@xyflow/react', async (loadActual) => {
  const actual = await loadActual<typeof import('@xyflow/react')>();
  return { ...actual, Handle: () => null, useUpdateNodeInternals: () => () => undefined };
});
vi.mock('@/features/canvas/ui/NodeHeader', () => ({ NODE_HEADER_FLOATING_POSITION_CLASS: '', NodeHeader: () => null }));

const resources = Object.fromEntries(['zh', 'en'].map((language) => [language,
  { translation: JSON.parse(readFileSync(`public/locales/${language}/translation.json`, 'utf8')) }])) as Record<string, { translation: object }>;

describe('video director with real locale resources', () => {
  beforeEach(() => useCanvasStore.setState({ nodes: [], edges: [] }));
  it('shows a changed-input marker for a completed result and returns to inputs', async () => {
    const i18n = i18next.createInstance();
    await i18n.use(initReactI18next).init({ lng: 'en', fallbackLng: 'en', resources });
    const frozen = createDirectorDraft('s1');
    const draft = { ...frozen, segments: frozen.segments.map((segment) => ({ ...segment, prompt: 'edited' })) };
    const data: VideoDirectorNodeData = { draft, activeInputMode: 'ref', activeAttemptId: 'a1',
      videoUrl: '/video.mp4', resultRevision: 0 };
    const attempt: DirectorAttempt = { id: 'a1', projectId: 'demo', canvasId: 'canvas', nodeId: 'director', requestId: 'r1',
      parentAttemptId: null, revision: 0, snapshot: frozen, stage: 'completed', optimized: null, rulesHash: null,
      referenceLimit: 5, workflowId: null, workflowProfileId: null, workflowProfileVersion: null,
      actualParameters: null, taskId: null, providerTaskId: null, resultUrl: '/video.mp4', error: null,
      failedStage: null, createdAt: null, updatedAt: null };
    useTask.mockReturnValue({ capabilities: null, attempts: [attempt], error: '', fieldErrors: {},
      generate: vi.fn(), recoverPending: vi.fn(), retry: vi.fn(), refresh: vi.fn() });
    render(<I18nextProvider i18n={i18n}><VideoDirectorNode id="director" type="videoDirectorNode" data={data}
      selected={false} dragging={false} draggable selectable deletable zIndex={0} isConnectable
      positionAbsoluteX={0} positionAbsoluteY={0} /></I18nextProvider>);
    expect(screen.getByText('Inputs modified')).toBeInTheDocument();
    expect(document.querySelector('video')).toHaveAttribute('controls');
    fireEvent.click(screen.getByRole('button', { name: 'Back to inputs' }));
    expect(screen.getByRole('textbox', { name: 'Current segment prompt' })).toHaveValue('edited');
    fireEvent.click(screen.getByRole('button', { name: 'View video' }));
    expect(document.querySelector('video')).toHaveAttribute('controls');
  });
  it('blocks generation for an unavailable connected reference even when a manual reference is valid', async () => {
    const i18n = i18next.createInstance();
    await i18n.use(initReactI18next).init({ lng: 'en', fallbackLng: 'en', resources });
    const draft = createDirectorDraft('s1');
    draft.references = [{ imageId: 'manual', url: '/manual.png' }];
    draft.segments[0].prompt = 'A scene';
    const data: VideoDirectorNodeData = { draft, activeInputMode: 'ref', activeAttemptId: null, videoUrl: null, resultRevision: null };
    useCanvasStore.setState({ nodes: [{ id: 'director', type: CANVAS_NODE_TYPES.videoDirector, position: { x: 0, y: 0 }, data }],
      edges: [{ id: 'missing', source: 'gone', target: 'director', data: { edgeKind: 'videoDirectorImage', slot: { kind: 'reference' } } }] } as never);
    const generate = vi.fn();
    useTask.mockReturnValue({ capabilities: { models: [{ id: draft.modelId }], params: { aspectRatio: [draft.aspectRatio], resolution: [draft.resolution] },
      sizes: [], modes: [{ id: 'ref_only', supported: true }], effectiveReferenceLimit: 5 }, attempts: [], error: '', fieldErrors: {},
      generate, recoverPending: vi.fn(), retry: vi.fn(), refresh: vi.fn() });
    render(<I18nextProvider i18n={i18n}><VideoDirectorNode id="director" type="videoDirectorNode" data={data}
      selected={false} dragging={false} draggable selectable deletable zIndex={0} isConnectable
      positionAbsoluteX={0} positionAbsoluteY={0} onOpenEditor={vi.fn()} /></I18nextProvider>);
    expect(screen.getByRole('alert')).toHaveTextContent('Connected reference image is unavailable');
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
    expect(generate).not.toHaveBeenCalled();
  });
  it('renders a connected reference without manual image actions', async () => {
    const i18n = i18next.createInstance();
    await i18n.use(initReactI18next).init({ lng: 'en', fallbackLng: 'en', resources });
    const draft = createDirectorDraft('s1');
    const data: VideoDirectorNodeData = { draft, activeInputMode: 'ref', activeAttemptId: null, videoUrl: null, resultRevision: null };
    useCanvasStore.setState({ nodes: [{ id: 'director', type: CANVAS_NODE_TYPES.videoDirector, position: { x: 0, y: 0 }, data },
      { id: 'source', type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl: '/linked.png' } }],
      edges: [{ id: 'link', source: 'source', target: 'director', data: { edgeKind: 'videoDirectorImage', slot: { kind: 'reference' } } }] } as never);
    useTask.mockReturnValue({ capabilities: null, attempts: [], error: '', fieldErrors: {},
      generate: vi.fn(), recoverPending: vi.fn(), retry: vi.fn(), refresh: vi.fn() });
    render(<I18nextProvider i18n={i18n}><VideoDirectorNode id="director" type="videoDirectorNode" data={data}
      selected={false} dragging={false} draggable selectable deletable zIndex={0} isConnectable
      positionAbsoluteX={0} positionAbsoluteY={0} /></I18nextProvider>);
    const linkedImage = document.querySelector('img[src*="linked.png"]');
    expect(linkedImage).toBeInTheDocument();
    const group = linkedImage?.closest('[role="group"]') as HTMLElement;
    expect(within(group).queryByRole('button')).not.toBeInTheDocument();
    expect(within(group).getByText('Connected image')).toBeInTheDocument();
  });
  it('shows a bound first frame as read-only and restores the manual frame after disconnect', async () => {
    const i18n = i18next.createInstance();
    await i18n.use(initReactI18next).init({ lng: 'en', fallbackLng: 'en', resources });
    const draft = createDirectorDraft('s1');
    draft.segments[0].firstFrame = { imageId: 'manual', url: '/manual.png' };
    const data: VideoDirectorNodeData = { draft, activeInputMode: 'frames', activeAttemptId: null, videoUrl: null, resultRevision: null };
    useCanvasStore.setState({ nodes: [{ id: 'director', type: CANVAS_NODE_TYPES.videoDirector, position: { x: 0, y: 0 }, data },
      { id: 'source', type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl: '/linked-frame.png' } }],
      edges: [{ id: 'link', source: 'source', target: 'director', data: { edgeKind: 'videoDirectorImage',
        slot: { kind: 'firstFrame', segmentId: 's1' } } }] } as never);
    useTask.mockReturnValue({ capabilities: null, attempts: [], error: '', fieldErrors: {},
      generate: vi.fn(), recoverPending: vi.fn(), retry: vi.fn(), refresh: vi.fn() });
    render(<I18nextProvider i18n={i18n}><VideoDirectorNode id="director" type="videoDirectorNode" data={data}
      selected={false} dragging={false} draggable selectable deletable zIndex={0} isConnectable
      positionAbsoluteX={0} positionAbsoluteY={0} /></I18nextProvider>);
    const first = screen.getByRole('group', { name: 'First frame' });
    expect(within(first).getByRole('img')).toHaveAttribute('src', expect.stringContaining('/linked-frame.png'));
    expect(within(first).queryByRole('button')).not.toBeInTheDocument();
    expect(within(first).getByText('Connected image')).toBeInTheDocument();
    act(() => useCanvasStore.setState({ nodes: useCanvasStore.getState().nodes.filter((node) => node.id === 'director') }));
    expect(within(screen.getByRole('group', { name: 'First frame' })).queryByRole('img')).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Connected frame image is unavailable');
    act(() => useCanvasStore.setState({ edges: [] }));
    const manual = screen.getByRole('group', { name: 'First frame' });
    expect(within(manual).getByRole('img')).toHaveAttribute('src', '/manual.png');
    expect(within(manual).getByRole('button', { name: 'Remove' })).toBeInTheDocument();
  });
  it('translates image action errors in English', async () => {
    const i18n = i18next.createInstance();
    await i18n.use(initReactI18next).init({ lng: 'en', fallbackLng: 'en', resources });
    window.history.replaceState({}, '', '/freezone');
    const data: VideoDirectorNodeData = { draft: createDirectorDraft('s1'), activeInputMode: 'ref',
      activeAttemptId: null, videoUrl: null, resultRevision: null };
    useCanvasStore.setState({ nodes: [{ id: 'director', type: CANVAS_NODE_TYPES.videoDirector,
      position: { x: 0, y: 0 }, data }], edges: [] } as never);
    useTask.mockReturnValue({ capabilities: null, attempts: [], error: '', fieldErrors: {},
      generate: vi.fn(), recoverPending: vi.fn(), retry: vi.fn(), refresh: vi.fn() });
    render(<I18nextProvider i18n={i18n}><VideoDirectorNode id="director" type="videoDirectorNode" data={data}
      selected={false} dragging={false} draggable selectable deletable zIndex={0} isConnectable
      positionAbsoluteX={0} positionAbsoluteY={0} /></I18nextProvider>);
    fireEvent.change(screen.getByLabelText('Upload Subject references image'),
      { target: { files: [new File(['image'], 'source.png', { type: 'image/png' })] } });
    expect(screen.getByRole('alert')).toHaveTextContent('Cannot upload image without a project');
  });
  it('routes a blank Ref card drop to references even when there are no segments', async () => {
    const i18n = i18next.createInstance();
    await i18n.use(initReactI18next).init({ lng: 'en', fallbackLng: 'en', resources });
    window.history.replaceState({}, '', '/freezone');
    const draft = createDirectorDraft('s1');
    draft.segments = [];
    const data: VideoDirectorNodeData = { draft, activeInputMode: 'ref', activeAttemptId: null,
      videoUrl: null, resultRevision: null };
    useCanvasStore.setState({ nodes: [{ id: 'director', type: CANVAS_NODE_TYPES.videoDirector,
      position: { x: 0, y: 0 }, data }], edges: [] } as never);
    useTask.mockReturnValue({ capabilities: null, attempts: [], error: '', fieldErrors: {},
      generate: vi.fn(), recoverPending: vi.fn(), retry: vi.fn(), refresh: vi.fn() });
    render(<I18nextProvider i18n={i18n}><VideoDirectorNode id="director" type="videoDirectorNode" data={data}
      selected={false} dragging={false} draggable selectable deletable zIndex={0} isConnectable
      positionAbsoluteX={0} positionAbsoluteY={0} /></I18nextProvider>);
    fireEvent.drop(screen.getByText('MiniMax H3 Ref'), { dataTransfer: { files: [new File(['image'], 'ref.png', { type: 'image/png' })] } });
    expect(screen.getByRole('alert')).toHaveTextContent('Cannot upload image without a project');
  });
  it.each([
    { language: 'zh', empty: 'Ref 引导，待输入', segments: '1 段', duration: '5.0 秒', edit: '编辑', stage: '优化中', title: '视频导演 · director', model: '模型' },
    { language: 'en', empty: 'Ref guided, awaiting input', segments: '1 segments', duration: '5.0 s', edit: 'Edit', stage: 'Optimizing', title: 'Video Director · director', model: 'Model' },
  ])('renders node, status and expanded panel in $language', async ({ language, empty, segments, duration, edit, stage, title, model }) => {
    const i18n = i18next.createInstance();
    await i18n.use(initReactI18next).init({ lng: language, fallbackLng: language, resources, interpolation: { escapeValue: false } });
    const draft = createDirectorDraft('s1');
    const data: VideoDirectorNodeData = { draft, activeAttemptId: 'a1', videoUrl: null, resultRevision: null };
    const attempt: DirectorAttempt = { id: 'a1', projectId: 'demo', canvasId: 'canvas', nodeId: 'director', requestId: 'r1',
      parentAttemptId: null, revision: 0, snapshot: draft, stage: 'optimizing', optimized: null, rulesHash: null,
      referenceLimit: 5, workflowId: null, workflowProfileId: null, workflowProfileVersion: null,
      actualParameters: null, taskId: null, providerTaskId: null, resultUrl: null, error: null,
      failedStage: null, createdAt: null, updatedAt: null };
    useTask.mockReturnValue({ capabilities: { models: [], params: { aspectRatio: [], resolution: [] }, effectiveReferenceLimit: 5 },
      attempts: [attempt], error: '', fieldErrors: {}, generate: vi.fn(), recoverPending: vi.fn(), retry: vi.fn(), refresh: vi.fn() });
    render(<I18nextProvider i18n={i18n}><VideoDirectorNode id="director" type="videoDirectorNode" data={data}
      selected={false} dragging={false} draggable selectable deletable zIndex={0} isConnectable
      positionAbsoluteX={0} positionAbsoluteY={0} /></I18nextProvider>);
    expect(screen.getByText(empty)).toBeInTheDocument();
    const reference = screen.getByRole('group', { name: language === 'zh' ? '主体参考图' : 'Subject references' });
    expect(within(reference).getByRole('button', { name: language === 'zh' ? '选择图片' : 'Select image' })).toBeInTheDocument();
    expect(screen.getByText(segments)).toBeInTheDocument();
    expect(screen.getByText(duration)).toBeInTheDocument();
    expect(screen.getByText(stage)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: edit }));
    expect(await screen.findByText(title)).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: model })).toBeInTheDocument();
    if (language === 'en') expect(screen.getByText('Active route: MiniMax H3 Ref')).toBeInTheDocument();
    expect(screen.getAllByText(stage).length).toBeGreaterThan(1);
  });
});
