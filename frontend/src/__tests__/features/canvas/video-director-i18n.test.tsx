import { readFileSync } from 'node:fs';
import i18next from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { VideoDirectorNode } from '@/features/canvas/nodes/VideoDirectorNode';
import { createDirectorDraft } from '@/features/canvas/domain/videoDirectorDraft';
import type { DirectorAttempt, VideoDirectorNodeData } from '@/features/canvas/domain/canvasNodes';

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
    expect(screen.getAllByText(stage).length).toBeGreaterThan(1);
  });
});
