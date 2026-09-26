import { readFileSync } from 'node:fs';
import i18next from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/__mocks__/msw/server';
import { VideoDirectorNode } from '@/features/canvas/nodes/VideoDirectorNode';
import { createDirectorDraft } from '@/features/canvas/domain/videoDirectorDraft';
import { CANVAS_NODE_TYPES, type VideoDirectorNodeData } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';

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

  it('selects character variants, submits ordered raw segments, then shows optimized history and video', async () => {
    let postedDraft: Record<string, any> | undefined;
    let storedAttempt: Record<string, any> | undefined;
    let resumeCount = 0;
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
      http.post(`${endpoint}/attempts/attempt-1/resume`, () => {
        resumeCount += 1;
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
    const editors = screen.getAllByRole('textbox');
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
