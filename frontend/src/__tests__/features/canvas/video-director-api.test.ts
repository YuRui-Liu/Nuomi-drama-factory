import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { createDirectorAttempt, getDirectorCapabilities, listDirectorAttempts } from '@/api/videoDirector';
import { createDirectorDraft } from '@/features/canvas/domain/videoDirectorDraft';

vi.mock('@/api/client', async (loadActual) => {
  const actual = await loadActual<typeof import('@/api/client')>();
  return { ...actual, apiCall: (path: string, options?: Parameters<typeof actual.apiCall>[1]) =>
    actual.apiCall(path, { ...options, prefix: 'http://localhost/api/v1' }) };
});

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const base = '*/api/v1/projects/demo/freezone/video-director';
const rawAttempt = { id: 'a1', project_id: 'demo', canvas_id: 'canvas', node_id: 'node', request_id: 'req',
  parent_attempt_id: null, revision: 3, stage: 'optimizing', result_url: null, created_at: '2026-01-01T00:00:00Z',
  snapshot: { schema_version: 1, revision: 3, model_id: 'minimax-h3', aspect_ratio: '9:16', resolution: '720p',
    references: [{ image_id: 'ref', url: '/ref.png', asset_id: null }],
    segments: [{ id: 's1', prompt: 'Original', duration_seconds: 5, first_frame: null, last_frame: null }] } };

describe('video director API', () => {
  it('converts capabilities and submits an immutable snake_case draft through the envelope', async () => {
    let request: unknown;
    server.use(
      http.get(`${base}/capabilities`, () => HttpResponse.json({ ok: true, data: {
        models: [{ id: 'minimax-h3', label: 'MiniMax H3', adapter: 'h3', reference_adapter: 'h3_ref' }],
        reference_limit: 5, effective_reference_limit: 5, configured_reference_limit: 5,
        fps: 24, frame_step: 17, frame_offset: 5, params: { resolution: ['720p'], aspect_ratio: ['9:16'] }, sizes: [], modes: [] } })),
      http.post(`${base}/attempts`, async ({ request: req }) => { request = await req.json(); return HttpResponse.json({ ok: true, data: { attempt_id: 'a1', task_id: 't1', attempt: rawAttempt } }, { status: 202 }); }),
    );
    const caps = await getDirectorCapabilities('demo');
    expect(caps.models[0].referenceAdapter).toBe('h3_ref');
    expect(caps.frameStep).toBe(17);
    const draft = createDirectorDraft('s1');
    draft.revision = 3;
    draft.references = [{ imageId: 'ref', url: '/ref.png' }];
    draft.segments[0].prompt = 'Original';
    const attempt = await createDirectorAttempt('demo', 'canvas', 'node', 'req', draft);
    expect(request).toMatchObject({ canvas_id: 'canvas', node_id: 'node', request_id: 'req', draft: {
      schema_version: 1, model_id: 'minimax-h3', references: [{ image_id: 'ref', url: '/ref.png' }],
      segments: [{ id: 's1', prompt: 'Original', duration_seconds: 5, first_frame: null }] } });
    expect(attempt.snapshot.segments[0].prompt).toBe('Original');
  });

  it('lists immutable attempts scoped to canvas and node', async () => {
    let query = '';
    server.use(http.get(`${base}/attempts`, ({ request }) => { query = new URL(request.url).search;
      return HttpResponse.json({ ok: true, data: { attempts: [rawAttempt] } }); }));
    expect((await listDirectorAttempts('demo', 'canvas', 'node'))[0].requestId).toBe('req');
    expect(query).toContain('canvas_id=canvas');
    expect(query).toContain('node_id=node');
  });
});
