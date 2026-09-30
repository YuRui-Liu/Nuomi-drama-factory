import { readFileSync } from 'node:fs';
import i18next from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/__mocks__/msw/server';
import { VideoDirectorNode } from '@/features/canvas/nodes/VideoDirectorNode';
import { createDirectorDraft } from '@/features/canvas/domain/videoDirectorDraft';
import { CANVAS_NODE_TYPES, type VideoDirectorNodeData } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import { getNodeDefinition } from '@/features/canvas/domain/nodeRegistry';

// Node's fetch cannot resolve ky's browser-relative prefix under jsdom.
vi.mock('@/api/client', async (loadActual) => {
  const actual = await loadActual<typeof import('@/api/client')>();
  return { ...actual, apiCall: async (path: string, options?: { method?: string; json?: unknown }) => {
    const response = await fetch(new URL(`/api/v1/${path}`, window.location.origin), {
      method: options?.method ?? 'GET',
      headers: options?.json ? { 'content-type': 'application/json' } : undefined,
      body: options?.json ? JSON.stringify(options.json) : undefined,
    });
    const envelope = await response.json();
    if (!response.ok || !envelope.ok) throw new Error(envelope.error ?? `HTTP ${response.status}`);
    return envelope.data;
  } };
});

vi.mock('@xyflow/react', async (loadActual) => {
  const actual = await loadActual<typeof import('@xyflow/react')>();
  return { ...actual, Handle: () => null, useUpdateNodeInternals: () => () => undefined };
});
vi.mock('@/features/canvas/ui/NodeHeader', () => ({ NODE_HEADER_FLOATING_POSITION_CLASS: '', NodeHeader: () => null }));

const i18n = i18next.createInstance();
const resources = { zh: { translation: JSON.parse(readFileSync('public/locales/zh/translation.json', 'utf8')) } };
await i18n.use(initReactI18next).init({ lng: 'zh', fallbackLng: 'zh', resources, interpolation: { escapeValue: false } });

const endpoint = '*/api/v1/projects/demo/freezone/video-director';
const library = [{ id: 'character:hero', name: 'Hero', media: 'image', source: 'character',
  image_urls: ['/base.png'], images: [
    { image_id: 'base', character_id: 'hero', kind: 'base', asset_kind: 'portrait',
      variant_id: null, variant_label: null, url: '/base.png' },
    { image_id: 'costume', character_id: 'hero', kind: 'variant', asset_kind: 'identity_costume',
      variant_id: 'coat', variant_label: '红外套', url: '/costume.png' },
  ] }];
const capabilities = { models: [{ id: 'minimax-h3', label: 'MiniMax H3', adapter: 'h3', reference_adapter: 'h3_ref' }],
  reference_limit: 5, effective_reference_limit: 5, configured_reference_limit: 5,
  fps: 24, frame_step: 17, frame_offset: 5,
  params: { resolution: ['720p'], aspect_ratio: ['9:16'] }, sizes: [],
  modes: [{ id: 'ref_only', supported: true }] };

function CurrentNode() {
  const data = useCanvasStore((state) => state.nodes[0].data as VideoDirectorNodeData);
  return <I18nextProvider i18n={i18n}><VideoDirectorNode id="director" type="videoDirectorNode"
    data={data} selected={false} dragging={false} draggable selectable deletable zIndex={0}
    isConnectable positionAbsoluteX={0} positionAbsoluteY={0} /></I18nextProvider>;
}
const nodeData = () => useCanvasStore.getState().nodes[0].data as VideoDirectorNodeData;

describe('Director real component flow', () => {
  beforeEach(() => {
    localStorage.clear();
    window.history.replaceState({}, '', '/projects/demo/freezone?canvas=canvas');
    useCanvasStore.setState({ userEditsSinceHydrate: 0, nodes: [{ id: 'director',
      type: CANVAS_NODE_TYPES.videoDirector, position: { x: 0, y: 0 },
      data: { displayName: 'Director', draft: createDirectorDraft('opening'), activeAttemptId: null,
        videoUrl: null, resultRevision: null, pendingSubmission: null } }] as never });
  });

  it('starts in Ref mode and exposes image and prompt inputs on the card', async () => {
    expect(getNodeDefinition(CANVAS_NODE_TYPES.videoDirector).createDefaultData().activeInputMode).toBe('ref');
    server.use(http.get(`${endpoint}/capabilities`, () => HttpResponse.json({ ok: true, data: capabilities })),
      http.get(`${endpoint}/attempts`, () => HttpResponse.json({ ok: true, data: { attempts: [] } })));
    render(<CurrentNode />);
    expect(screen.getByText('Ref 引导，待输入')).toBeInTheDocument();
    expect(screen.getByRole('group', { name: '主体参考图' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: '首帧' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: '尾帧' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '当前片段提示词' })).toHaveValue('');
    expect(screen.getByText('MiniMax H3 Ref')).toBeInTheDocument();
  });

  it('opens the editor when card generation finds an invalid draft', async () => {
    let capabilitiesLoaded = false;
    server.use(http.get(`${endpoint}/capabilities`, () => { capabilitiesLoaded = true; return HttpResponse.json({ ok: true, data: capabilities }); }),
      http.get(`${endpoint}/attempts`, () => HttpResponse.json({ ok: true, data: { attempts: [] } })));
    render(<CurrentNode />);
    await waitFor(() => expect(capabilitiesLoaded).toBe(true));
    await act(async () => { await Promise.resolve(); });
    fireEvent.click(screen.getByRole('button', { name: '生成' }));
    expect(await screen.findByText('没有参考图时必须选择首帧')).toBeInTheDocument();
  });

  it('switches card segment without rewriting another prompt', () => {
    const draft = createDirectorDraft('first');
    draft.segments.push({ ...draft.segments[0], id: 'second', prompt: 'second prompt' });
    useCanvasStore.getState().updateNodeData('director', { draft });
    render(<CurrentNode />);
    fireEvent.click(screen.getByRole('button', { name: '片段 2' }));
    fireEvent.change(screen.getByRole('textbox', { name: '当前片段提示词' }), { target: { value: 'revised second' } });
    expect(nodeData().visibleSegmentId).toBe('second');
    expect(nodeData().draft.segments.map((segment) => segment.prompt)).toEqual(['', 'revised second']);
  });

  it('keeps the first frame when switching back to Ref', () => {
    const draft = createDirectorDraft('first');
    draft.segments[0].firstFrame = { imageId: 'frame', url: '/frame.png' };
    useCanvasStore.getState().updateNodeData('director', { draft, activeInputMode: 'frames' });
    render(<CurrentNode />);
    fireEvent.click(screen.getByRole('button', { name: 'Ref' }));
    expect(nodeData().activeInputMode).toBe('ref');
    expect(nodeData().draft.segments[0].firstFrame?.imageId).toBe('frame');
    expect(screen.getByText('已保留，当前不参与生成')).toBeInTheDocument();
  });

  it('switches to frames when a first frame is selected without references', async () => {
    const response = (data: unknown) => HttpResponse.json({ ok: true, data });
    server.use(http.get('*/api/v1/projects/demo/freezone/video/character-library', () => response(library)),
      http.post('*/api/v1/projects/demo/freezone/video/asset-library/sync-from-mainline', () => response(library)));
    render(<CurrentNode />);
    fireEvent.click(within(screen.getByRole('group', { name: '首帧' })).getByRole('button', { name: '选择图片' }));
    fireEvent.click(await screen.findByRole('button', { name: '查看Hero的图片' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Hero 基础图 基础肖像' }));
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    await waitFor(() => expect(nodeData().draft.segments[0].firstFrame?.imageId).toBe('base'));
    expect(nodeData().activeInputMode).toBe('frames');
    fireEvent.click(screen.getByRole('button', { name: 'Ref' }));
    expect(nodeData().draft.segments[0].firstFrame?.imageId).toBe('base');
  });

  it('asks for a target before dropping onto an occupied card', () => {
    const draft = createDirectorDraft('first');
    draft.references = [{ imageId: 'reference', url: '/reference.png' }];
    useCanvasStore.getState().updateNodeData('director', { draft, activeInputMode: 'ref' });
    render(<CurrentNode />);
    fireEvent.drop(screen.getByText('MiniMax H3 Ref'), { dataTransfer: { files: [new File(['image'], 'new.png', { type: 'image/png' })] } });
    expect(screen.getByRole('dialog', { name: '选择图片放置目标' })).toBeInTheDocument();
    expect(nodeData().draft.references).toHaveLength(1);
  });

  it('selects character variants, submits ordered raw segments, then shows optimized history and video', async () => {
    let postedDraft: Record<string, any> | undefined;
    let storedAttempt: Record<string, any> | undefined;
    let resumeCount = 0;
    let finishResume!: () => void;
    const resumeGate = new Promise<void>((resolve) => { finishResume = resolve; });
    const response = (data: unknown) => HttpResponse.json({ ok: true, data });
    server.use(
      http.get('*/api/v1/projects/demo/freezone/video/character-library', () => response(library)),
      http.post('*/api/v1/projects/demo/freezone/video/asset-library/sync-from-mainline', () => response(library)),
      http.get(`${endpoint}/capabilities`, () => response(capabilities)),
      http.get(`${endpoint}/attempts`, () => response({ attempts: storedAttempt ? [storedAttempt] : [] })),
      http.post(`${endpoint}/attempts`, async ({ request }) => {
        const body = await request.json() as Record<string, any>;
        postedDraft = body.draft;
        storedAttempt = { id: 'attempt-1', project_id: 'demo', canvas_id: 'canvas', node_id: 'director',
          request_id: body.request_id, parent_attempt_id: null, revision: body.draft.revision,
          snapshot: body.draft, stage: 'optimizing', optimized: null, rules_hash: null,
          reference_limit: 5, workflow_id: null, workflow_profile_id: null,
          workflow_profile_version: null, actual_parameters: null, task_id: null,
          provider_task_id: null, result_url: null, error: null, failed_stage: null,
          created_at: '2026-09-26T00:00:00Z', updated_at: '2026-09-26T00:00:00Z' };
        return response({ attempt: storedAttempt });
      }),
      http.get(`${endpoint}/attempts/attempt-1`, () => response({ attempt: storedAttempt })),
      http.post(`${endpoint}/attempts/attempt-1/resume`, async () => {
        resumeCount += 1;
        await resumeGate;
        storedAttempt = { ...storedAttempt, stage: 'completed', provider_task_id: 'fake-task',
          result_url: '/static/projects/demo/video.mp4', workflow_id: '2096502793044582401',
          workflow_profile_id: 'h3-ref', workflow_profile_version: 1,
          actual_parameters: { route: 'h3_ref', frames: 248, duration_seconds: 248 / 24 },
          optimized: { revision: postedDraft?.revision, route: 'h3_ref', profile_id: 'minimax-h3-director',
            profile_version: 15, optimized_at: '2026-09-26T00:00:00Z',
            segments: postedDraft?.segments.map((segment: Record<string, any>) => ({
              segment_id: segment.id, mode: 'ref2va', requested_duration_seconds: segment.duration_seconds,
              duration_seconds: 124 / 24, frames: 124, wire: {}, prompt: `Directed: ${segment.prompt}` })) },
          updated_at: '2026-09-26T00:00:01Z' };
        return response({ attempt: storedAttempt });
      }),
    );

    render(<CurrentNode />);
    fireEvent.click(screen.getByRole('button', { name: '编辑' }));
    expect(await screen.findByText('视频导演 · director')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: '生成视频' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '选择参考图' }));
    fireEvent.click(await screen.findByRole('button', { name: '查看Hero的图片' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Hero 基础图 基础肖像' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Hero 红外套 服装参考图' }));
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    await waitFor(() => expect(nodeData().draft.references).toHaveLength(2));
    expect(nodeData().draft.references.map((image) => [image.imageId, image.characterId, image.variantId])).toEqual([
      ['base', 'hero', null], ['costume', 'hero', 'coat'],
    ]);
    fireEvent.click(screen.getByRole('button', { name: '添加分段' }));
    const editors = screen.getAllByRole('textbox').slice(-2);
    fireEvent.change(editors[0], { target: { value: 'Opening original' } });
    fireEvent.change(editors[1], { target: { value: 'Closing original' } });
    fireEvent.click(screen.getByRole('button', { name: '生成视频' }));
    await waitFor(() => expect(postedDraft).toBeDefined());
    expect(postedDraft?.references.map((image: Record<string, any>) =>
      [image.image_id, image.character_id, image.variant_id, image.variant_label, image.asset_kind])).toEqual([
      ['base', 'hero', null, null, 'portrait'],
      ['costume', 'hero', 'coat', '红外套', 'identity_costume'],
    ]);
    expect(postedDraft?.segments.map((segment: Record<string, any>) => segment.prompt)).toEqual([
      'Opening original', 'Closing original',
    ]);
    await waitFor(() => expect(resumeCount).toBe(1));
    await waitFor(() => expect(screen.getAllByText('优化中').length).toBeGreaterThan(0));
    expect(screen.getByRole('button', { name: '生成视频' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: /批准|审核|确认优化/ })).not.toBeInTheDocument();
    expect(nodeData().videoUrl).toBeNull();
    await act(async () => { finishResume(); });
    await waitFor(() => expect(nodeData().videoUrl).toBe('/static/projects/demo/video.mp4'));
    expect(nodeData().draft.segments.map((segment) => segment.prompt)).toEqual([
      'Opening original', 'Closing original',
    ]);
    const video = document.querySelector('video');
    expect(video).toHaveAttribute('controls');
    expect(video).toHaveAttribute('src', expect.stringContaining('/static/projects/demo/video.mp4'));
    const history = screen.getByRole('region', { name: '生成历史' });
    fireEvent.click(within(history).getByText(/已完成/));
    expect(within(history).getByText(/Opening original \/ Closing original/)).toBeInTheDocument();
    expect(within(history).getByText(/Directed: Opening original \/ Directed: Closing original/)).toBeInTheDocument();
    expect(within(history).getAllByText(/h3_ref/)).toHaveLength(2);
    expect(within(history).getByRole('link', { name: '查看结果' })).toHaveAttribute('href', '/static/projects/demo/video.mp4');
  });
});
