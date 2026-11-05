import { useCallback, useEffect, useRef, useState, type ComponentType } from 'react';
import { createRoot } from 'react-dom/client';
import type { DirectorPreviewGroup } from '../features/canvas/production/DirectorShotViews';
import type { ProductionProgress, ProductionStageName, ProductionStageProgress } from '../features/canvas/production/productionProgressApi';
import type { FreezoneCanvasPayload } from '../api/canvas';
import type { CanvasEdge, CanvasNode } from '../features/canvas/domain/canvasNodes';
import { registerFreezoneCanvasRuntime } from '../features/freezone/canvasSyncRuntime';
import type { ExternalCanvasOperation } from '../features/freezone/externalCanvasOperation';
import { useCanvasStore } from '../stores/canvasStore';
import { useShotMetadataStore } from '../features/freezone/shotMetadataStore';
import '../index.css';
import './director-production-demo.css';

const PROJECT = 'nuomi-demo';
const CANVAS = 'director-demo-e01';
const EPISODE = '001';
const EPISODE_NUMBER = 1;
const REVISION = 'demo-revision-e01';
const SCREENPLAY_PATH = `episodes/${EPISODE}/screenplay-fixture.md`;
const SCREENPLAY_ID = 'demo-fixture-screenplay-receipt';
const API_ROOT = `/api/v1/projects/${PROJECT}`;
const PROGRESS_PATH = `${API_ROOT}/freezone/orchestration/canvases/${CANVAS}/progress/${EPISODE}`;
const CANVAS_PATH = `${API_ROOT}/freezone/canvases/${CANVAS}`;
const GROUPS = [
  {
    id: 'white-tail-leak',
    title: '白尾漏雨 · 步知遥与朏朏',
    shots: [
      { id: 'w1', subject: '步知遥', description: '雨水沿破瓦落进屋内；步知遥抬手挡住朏朏头顶的漏点。', duration_seconds: 3.2 },
      { id: 'w2', subject: '朏朏', description: '朏朏从旧纸伞下探出白尾，追着水滴看向门外。', duration_seconds: 2.6 },
    ],
    grid: 'episodes/001/grids/white-tail-leak.svg',
  },
  {
    id: 'collector-at-eaves',
    title: '檐下交接 · 收简人',
    shots: [
      { id: 'c1', subject: '收简人', description: '收简人停在门槛外，不越过屋檐下的干线；先把湿简放到石阶。', duration_seconds: 3.8 },
      { id: 'c2', subject: '步知遥', description: '步知遥没有接简，只把纸伞向朏朏一侧倾过去。', duration_seconds: 2.9 },
    ],
    grid: 'episodes/001/grids/collector-at-eaves.svg',
  },
] as const;

type FixtureStage = ProductionStageProgress;
type FixtureMediaGroup = DirectorPreviewGroup & {
  stages: DirectorPreviewGroup['stages'] & Record<string, Record<string, unknown> | undefined>;
};
type FixtureRun = { id: string; canvas_id: string; status: string; target: string; attempts: Record<string, unknown[]> };
type RequestNote = { method: string; path: string; result: 'mocked' | 'blocked' };
type FixtureState = {
  progress: ProductionProgress;
  canvas: FreezoneCanvasPayload;
  media: FixtureMediaGroup[];
  documents: Map<string, { path: string; text: string; hash: string }>;
  runs: Map<string, FixtureRun>;
  pendingVoiceTasks: Array<{ groupId: string; spanIndex: number; refreshes: number; taskId: string }>;
  runSequence: number;
  requests: RequestNote[];
  blockedRequests: string[];
};

function stage(status: FixtureStage['status'], outputs: Record<string, unknown> = {}, hash: string | null = null): FixtureStage {
  return {
    status,
    input_hash: hash,
    outputs,
    output_hash: null,
    approval_hash: null,
    approved_hash: status === 'ready' ? `demo-approved:${hash ?? status}` : null,
    review_resolution: status === 'ready' ? 'demo-fixture' : null,
    error: null,
  };
}

function newProgress(): ProductionProgress {
  return {
    episode_id: EPISODE,
    mode: 'review',
    stop_after: 'storyboard',
    blocked: null,
    stages: {
      screenplay: stage('ready', { script: { path: SCREENPLAY_PATH, sha256: SCREENPLAY_ID } }, SCREENPLAY_ID),
      assets: stage('ready', { reuse_checklist: { path: `episodes/${EPISODE}/asset-reuse-fixture.md`, sha256: 'demo-fixture-assets' } }, 'demo-fixture-assets'),
      storyboard: stage('pending'),
      video: stage('pending'),
      audio: stage('pending'),
      composition: stage('pending'),
    },
  };
}

function newMedia(): FixtureMediaGroup[] {
  return GROUPS.map(group => ({
    id: group.id,
    director_revision_id: REVISION,
    stages: {
      render: { status: 'pending', needs_regeneration: false },
      video: { status: 'pending', needs_regeneration: false },
    },
  }));
}

function newCanvas(): FreezoneCanvasPayload {
  return {
    schema_version: 2,
    canvas_id: CANVAS,
    project_id: PROJECT,
    revision: 1,
    nodes: [],
    edges: [],
    metadata: {
      shotMetadata: clone(useShotMetadataStore.getState().shot),
      viewportBookmarks: clone(useCanvasStore.getState().viewportBookmarks),
    },
  };
}

function createFixtureState(): FixtureState {
  return {
    progress: newProgress(),
    canvas: newCanvas(),
    media: newMedia(),
    documents: new Map(),
    runs: new Map(),
    pendingVoiceTasks: [],
    runSequence: 0,
    requests: [],
    blockedRequests: [],
  };
}

function resetFixtureState(target: FixtureState) {
  const clean = createFixtureState();
  Object.assign(target, clean);
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

function response(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });
}

function envelope(data: unknown, status = 200): Response {
  return response(status >= 400 ? { ok: false, error: '仅演示用的本地 fixture；该资源没有后端记录。' } : { ok: true, data }, status);
}

function bodyObject(value: unknown): Record<string, unknown> {
  if (typeof value !== 'string' || !value) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

function fixtureNode(id: string, type: string, data: Record<string, unknown>, x: number, y: number): CanvasNode {
  return { id, type, position: { x, y }, data } as CanvasNode;
}

function graphForState(state: FixtureState, body: Record<string, unknown>) {
  const episode = Number(body.episode ?? EPISODE_NUMBER);
  const revision = String(body.revision_id ?? REVISION);
  const scope = `demo-e${String(episode).padStart(3, '0')}`;
  const renderNodes: CanvasNode[] = [];
  const videoNodes: CanvasNode[] = [];
  const composeId = `${scope}-compose`;
  const edges: CanvasEdge[] = [];
  for (const [index, group] of GROUPS.entries()) {
    const renderId = `${scope}-${group.id}-render`;
    const videoId = `${scope}-${group.id}-video`;
    const binding = { episodeNumber: episode, directorRevisionId: revision, groupId: group.id };
    renderNodes.push(fixtureNode(renderId, 'narrativeGroupStoryboardNode', {
      ...binding, operation: 'render', displayName: `整组分镜 · ${group.title}`,
    }, 0, index * 300));
    videoNodes.push(fixtureNode(videoId, 'narrativeGroupVideoNode', {
      ...binding, operation: 'video', displayName: `整组视频 · ${group.title}`,
    }, 460, index * 300));
    edges.push({ id: `${renderId}-to-${videoId}`, source: renderId, target: videoId, data: { sourcePort: 'json', targetPort: 'json' } } as CanvasEdge);
    edges.push({ id: `${videoId}-to-${composeId}`, source: videoId, target: composeId, data: { sourcePort: 'json', targetPort: 'json' } } as CanvasEdge);
  }
  const compose = fixtureNode(composeId, 'episodeComposeNode', {
    episodeNumber: episode,
    directorRevisionId: revision,
    productionGroupIds: GROUPS.map(group => group.id),
    operation: 'compose',
    displayName: `E${String(episode).padStart(2, '0')} 整集成片`,
  }, 920, 150);
  state.canvas = {
    ...state.canvas,
    revision: (state.canvas.revision ?? 0) + 1,
    nodes: [...renderNodes, ...videoNodes, compose],
    edges,
  };
  return {
    target: composeId,
    node_ids: [...renderNodes, ...videoNodes, compose].map(node => node.id),
    submitted: false as const,
    canvas: clone(state.canvas),
  };
}

function updateMedia(state: FixtureState, groupId: string, patch: Record<string, unknown>) {
  state.media = state.media.map(group => group.id === groupId
    ? { ...group, stages: { ...group.stages, ...patch } } as FixtureMediaGroup
    : group);
}

function runFixtureOperation(state: FixtureState, body: Record<string, unknown>): FixtureRun {
  const target = String(body.target ?? '');
  const node = state.canvas.nodes.find(candidate => (candidate as { id?: string }).id === target) as CanvasNode | undefined;
  const operation = String(body.operation ?? '');
  const groupId = String(node?.data.groupId ?? '');
  const runId = `demo-run-${++state.runSequence}`;
  if (operation === 'render') {
    const group = GROUPS.find(candidate => candidate.id === groupId);
    if (group) updateMedia(state, groupId, {
      render: {
        status: 'completed',
        needs_regeneration: false,
        grid_asset: `/api/v1/projects/${PROJECT}/media/${group.grid}`,
      },
    });
  } else if (operation === 'video') {
    updateMedia(state, groupId, {
      video: {
        status: 'completed',
        revision: 1,
        needs_regeneration: false,
        video_asset: `/api/v1/projects/${PROJECT}/media/episodes/${EPISODE}/videos/${groupId}.mp4`,
        original_audio_path: `/api/v1/projects/${PROJECT}/media/episodes/${EPISODE}/audio/${groupId}-original.mp3`,
        video_spans: [{
          span_index: 0,
          beat_numbers: [1, 2],
          start_seconds: 0,
          end_seconds: 6.8,
          has_dialogue: true,
          dialogue_source: 'external_tts',
          external_audio_ready: false,
        }],
      },
    });
  } else if (operation === 'compose') {
    const resultUrl = `/api/v1/projects/${PROJECT}/media/episodes/${EPISODE}/composition/demo-preview.mp4`;
    state.canvas = {
      ...state.canvas,
      nodes: state.canvas.nodes.map(candidate => (candidate as { id?: string }).id === target
        ? { ...(candidate as CanvasNode), data: { ...(candidate as CanvasNode).data, resultVideoUrl: resultUrl } }
        : candidate),
    };
  }
  const run: FixtureRun = {
    id: runId,
    canvas_id: CANVAS,
    status: 'succeeded',
    target,
    attempts: { [target]: [{ status: 'succeeded', fixture: true }] },
  };
  state.runs.set(runId, run);
  return run;
}

function setStageComplete(state: FixtureState, body: Record<string, unknown>) {
  const name = String(body.stage) as ProductionStageName;
  if (!['screenplay', 'assets', 'storyboard', 'video', 'audio', 'composition'].includes(name)) return;
  const outputs = body.outputs && typeof body.outputs === 'object' && !Array.isArray(body.outputs)
    ? body.outputs as Record<string, unknown> : {};
  const outputRefs = (value: unknown, kind: string) => (Array.isArray(value) ? value : []).flatMap((item, index) => {
    const record = item && typeof item === 'object' && !Array.isArray(item) ? item as Record<string, unknown> : {};
    const path = typeof item === 'string' ? item : record.path;
    if (typeof path !== 'string' || !path.trim()) return [];
    return [{ path, sha256: typeof record.sha256 === 'string' ? record.sha256 : `demo-fixture-${kind}-${index + 1}` }];
  });
  const normalizedOutputs = name === 'audio'
    ? {
      ...clone(outputs),
      audio: outputRefs(outputs.audio, 'audio'),
      ...(Array.isArray(outputs.ambience) ? { ambience: outputRefs(outputs.ambience, 'ambience') } : {}),
    }
    : clone(outputs);
  const inputHash = String(body.input_hash ?? 'demo-fixture-input');
  const approvalHash = `demo-approval:${name}:${inputHash}`;
  const reviewStage = name === 'screenplay' || name === 'assets' || name === 'storyboard';
  const nextStage: FixtureStage = {
    status: reviewStage && state.progress.mode === 'review' ? 'awaiting_review' : 'ready',
    input_hash: inputHash,
    outputs: normalizedOutputs,
    output_hash: `demo-output:${name}`,
    approval_hash: reviewStage && state.progress.mode === 'review' ? approvalHash : null,
    approved_hash: null,
    review_resolution: null,
    error: null,
  };
  state.progress = {
    ...state.progress,
    stages: { ...state.progress.stages, [name]: nextStage },
  };
}

function installMockBoundary(
  state: FixtureState,
  onRequest: (note: RequestNote) => void,
  onMediaUpdate: (media: FixtureMediaGroup[]) => void,
) {
  const nativeFetch = window.fetch;
  const originalSetSrc = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
  const originalMediaSrc = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'src');
  const originalSetAttribute = Element.prototype.setAttribute;
  const nativeOpen = XMLHttpRequest.prototype.open;
  const nativeSend = XMLHttpRequest.prototype.send;
  const blockedXhrUrls = new WeakMap<XMLHttpRequest, string>();
  const svgGrid = (_title: string, kind: 'leak' | 'handoff') => {
    const action = kind === 'leak'
      ? `<path d="M50 110H710" stroke="#a4abb4" stroke-width="5"/><path d="M625 108v86m-56-45h112" stroke="#68a8c8" stroke-width="7"/><circle cx="680" cy="290" r="25" fill="#e5ff5c"/><path d="M655 308q25 33 50 0" fill="#e5ff5c"/><text x="50" y="61">雨线落进屋内 · 步知遥挡住漏点</text>`
      : `<path d="M355 75V385" stroke="#e5ff5c" stroke-width="8"/><path d="M80 110H650" stroke="#8e989f" stroke-width="6"/><circle cx="245" cy="205" r="30" fill="#70b5d1"/><path d="M245 235v100m-35-63h70" stroke="#70b5d1" stroke-width="12"/><circle cx="515" cy="212" r="28" fill="#d1aa77"/><path d="M515 240v95m-31-60h62" stroke="#d1aa77" stroke-width="12"/><rect x="410" y="315" width="72" height="13" rx="4" fill="#c89f62"/><text x="50" y="61">收简人守在檐外 · 纸简留在石阶</text>`;
    const detail = kind === 'leak'
      ? '<path d="M120 175v34m185 20v37m160-60v45m-335 82v38m360-135v25" stroke="#70b5d1" stroke-width="4" opacity=".85"/><path d="M55 350q100-22 200 0t200 0t200 0" fill="none" stroke="#7d888b" stroke-width="4"/>'
      : '<path d="M85 385H640M90 366H640" stroke="#79858b" stroke-width="4"/><path d="M425 332l53 0m-42-7l42 0" stroke="#c89f62" stroke-width="7"/>';
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 520" role="img"><defs><linearGradient id="night" x2="0" y2="1"><stop stop-color="#20282d"/><stop offset="1" stop-color="#0b0d10"/></linearGradient></defs><rect width="900" height="520" rx="16" fill="url(#night)"/><text x="38" y="38" fill="#e5ff5c" font-size="17" letter-spacing="3">E01 · LOCAL STORYBOARD STUDY</text><g transform="translate(30 78)"><rect width="840" height="400" rx="10" fill="#151a1e" stroke="#46515a"/><rect x="20" y="20" width="800" height="360" rx="5" fill="#11171b" stroke="#303a40"/><path d="M35 100H805M35 260H805" stroke="#303a40" stroke-width="2"/><g fill="#c3c8c7" font-size="22">${action}</g><g>${detail}</g><text x="50" y="425" fill="#9aa4aa" font-size="15">分镜构图示意 · 非模型生成素材 · 仅供交互审阅</text></g></svg>`;
  };
  const gridImages = new Map<string, string>([
    ['white-tail-leak', URL.createObjectURL(new Blob([svgGrid('白尾漏雨', 'leak')], { type: 'image/svg+xml' }))],
    ['collector-at-eaves', URL.createObjectURL(new Blob([svgGrid('檐下交接', 'handoff')], { type: 'image/svg+xml' }))],
  ]);

  const log = (method: string, path: string, result: RequestNote['result']) => {
    const note = { method, path, result } as RequestNote;
    state.requests = [note, ...state.requests].slice(0, 12);
    if (result === 'blocked') state.blockedRequests = [path, ...state.blockedRequests].slice(0, 12);
    onRequest(note);
  };

  const mockFetch: typeof window.fetch = async (input, init) => {
    const rawUrl = input instanceof Request ? input.url : String(input);
    const method = String(init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
    let url: URL;
    try { url = new URL(rawUrl, window.location.href); }
    catch { log(method, rawUrl, 'blocked'); return envelope(null, 403); }
    const path = `${url.pathname}${url.search}`;
    const requestBody = typeof init?.body === 'string'
      ? init.body
      : input instanceof Request ? await input.clone().text().catch(() => '') : '';
    const body = bodyObject(requestBody);
    let result: Response | null = null;

    if (url.origin === window.location.origin && url.pathname === PROGRESS_PATH && method === 'GET') {
      result = envelope(clone(state.progress));
    } else if (url.origin === window.location.origin && url.pathname === `${PROGRESS_PATH}/configure` && method === 'POST') {
      state.progress = {
        ...state.progress,
        mode: body.mode === 'auto' ? 'auto' : 'review',
        stop_after: typeof body.stop_after === 'string' ? body.stop_after as ProductionProgress['stop_after'] : null,
      };
      result = envelope(clone(state.progress));
    } else if (url.origin === window.location.origin && url.pathname === `${PROGRESS_PATH}/complete` && method === 'POST') {
      setStageComplete(state, body);
      result = envelope(clone(state.progress));
    } else if (url.origin === window.location.origin && url.pathname === `${PROGRESS_PATH}/approve` && method === 'POST') {
      const name = String(body.stage) as ProductionStageName;
      const current = state.progress.stages[name];
      if (current?.status === 'awaiting_review' && body.input_hash === current.approval_hash) {
        state.progress = {
          ...state.progress,
          stages: { ...state.progress.stages, [name]: { ...current, status: 'ready', approved_hash: current.approval_hash, review_resolution: 'approved-in-demo' } },
        };
        result = envelope(clone(state.progress));
      } else result = envelope(null, 409);
    } else if (url.origin === window.location.origin && url.pathname === `${API_ROOT}/freezone/orchestration/canvases/${CANVAS}/documents` && method === 'GET') {
      const pathValue = url.searchParams.get('path') ?? '';
      const document = state.documents.get(pathValue);
      result = document ? envelope(clone(document)) : envelope(null, 404);
    } else if (url.origin === window.location.origin && url.pathname === `${API_ROOT}/freezone/orchestration/canvases/${CANVAS}/documents` && method === 'PUT') {
      const documentPath = String(body.path ?? '');
      const documentText = String(body.text ?? '');
      if (!documentPath.startsWith(`episodes/${EPISODE}/`) || !documentText) result = envelope(null, 403);
      else {
        const document = { path: documentPath, text: documentText, hash: `demo-local-document-${state.documents.size + 1}` };
        state.documents.set(documentPath, document);
        result = envelope(clone(document));
      }
    } else if (url.origin === window.location.origin && url.pathname === CANVAS_PATH && method === 'GET') {
      result = envelope(clone(state.canvas));
    } else if (url.origin === window.location.origin && url.pathname === `${API_ROOT}/freezone/orchestration/canvases/${CANVAS}/production-graph` && method === 'POST') {
      result = envelope(graphForState(state, body));
    } else if (url.origin === window.location.origin && url.pathname === `${API_ROOT}/freezone/orchestration/runs` && method === 'POST') {
      result = envelope(runFixtureOperation(state, body));
      onMediaUpdate(clone(state.media));
    } else if (url.origin === window.location.origin && url.pathname.startsWith(`${API_ROOT}/freezone/orchestration/runs/`) && method === 'GET') {
      const runId = url.pathname.split('/').slice(-1)[0] ?? '';
      const run = state.runs.get(decodeURIComponent(runId));
      result = run ? envelope(clone(run)) : envelope(null, 404);
    } else if (url.origin === window.location.origin && url.pathname.startsWith(`${API_ROOT}/episodes/${EPISODE_NUMBER}/narrative-groups/`) && url.pathname.endsWith('/render/references') && method === 'GET') {
      result = envelope({ reference_revision: 'demo-fixture-references', bindings: [] });
    } else if (url.origin === window.location.origin && url.pathname.startsWith(`${API_ROOT}/episodes/${EPISODE_NUMBER}/narrative-groups/`) && url.pathname.endsWith('/video/dialogue-source') && method === 'POST') {
      const groupId = decodeURIComponent(url.pathname.split('/').slice(-3)[0] ?? '');
      const spanIndex = Number(body.span_index);
      const source = body.dialogue_source === 'external_tts' ? 'external_tts' : 'h3_native';
      const group = state.media.find(item => item.id === groupId);
      const video = group?.stages?.video;
      const spans = Array.isArray(video?.video_spans) ? video.video_spans : [];
      if (!group || !video || !Number.isInteger(spanIndex) || !spans[spanIndex]) result = envelope(null, 404);
      else {
        const nextSpans = spans.map((span, index) => index !== spanIndex ? span : {
          ...span,
          dialogue_source: source,
          external_audio_ready: false,
          external_audio_path: undefined,
        });
        if (source === 'external_tts') {
          const taskId = `demo-audio-task-${state.pendingVoiceTasks.length + 1}`;
          state.pendingVoiceTasks = state.pendingVoiceTasks.filter(task => task.groupId !== groupId || task.spanIndex !== spanIndex);
          state.pendingVoiceTasks.push({ groupId, spanIndex, refreshes: 0, taskId });
          updateMedia(state, groupId, { video: {
            ...video,
            video_spans: nextSpans,
            ambience_stem_path: undefined,
            ambience_stem_status: 'pending',
          } });
          onMediaUpdate(clone(state.media));
          result = envelope({ ok: true, data: { task_id: taskId, recomposition_deferred: true } });
        } else {
          state.pendingVoiceTasks = state.pendingVoiceTasks.filter(task => task.groupId !== groupId || task.spanIndex !== spanIndex);
          updateMedia(state, groupId, { video: { ...video, video_spans: nextSpans } });
          onMediaUpdate(clone(state.media));
          result = envelope({ ok: true, data: { task_id: null, recomposition_deferred: true } });
        }
      }
    }

    if (!result) {
      log(method, path, 'blocked');
      return envelope(null, 403);
    }
    log(method, path, 'mocked');
    return result;
  };

  window.fetch = mockFetch;

  const localGridImage = (value: unknown) => {
    if (typeof value !== 'string') return null;
    if (gridImages.has(value)) return value;
    let url: URL;
    try { url = new URL(value, window.location.origin); } catch { return null; }
    if (url.origin !== window.location.origin) return null;
    if (url.pathname === `/static/projects/${PROJECT}/episodes/${EPISODE}/grids/white-tail-leak.svg`) return gridImages.get('white-tail-leak') ?? null;
    if (url.pathname === `/static/projects/${PROJECT}/episodes/${EPISODE}/grids/collector-at-eaves.svg`) return gridImages.get('collector-at-eaves') ?? null;
    return null;
  };
  const safeSetSrc = function (this: HTMLImageElement, value: string) {
    const local = localGridImage(value);
    if (local && originalSetSrc?.set) originalSetSrc.set.call(this, local);
    else {
      this.removeAttribute('src');
      log('IMG', String(value), 'blocked');
    }
  };
  const blockMediaSrc = function (this: HTMLMediaElement, value: string) {
    this.removeAttribute('src');
    log('MEDIA', String(value), 'blocked');
  };
  if (originalSetSrc?.get && originalSetSrc.set) {
    Object.defineProperty(HTMLImageElement.prototype, 'src', {
      ...originalSetSrc,
      set: safeSetSrc,
    });
  }
  if (originalMediaSrc?.get && originalMediaSrc.set) {
    Object.defineProperty(HTMLMediaElement.prototype, 'src', {
      ...originalMediaSrc,
      set: blockMediaSrc,
    });
  }
  Element.prototype.setAttribute = function (this: Element, name: string, value: string) {
    if (this instanceof HTMLImageElement && name.toLowerCase() === 'src') {
      safeSetSrc.call(this, value);
      return;
    }
    if (this instanceof HTMLMediaElement && name.toLowerCase() === 'src') {
      blockMediaSrc.call(this, value);
      return;
    }
    originalSetAttribute.call(this, name, value);
  };

  XMLHttpRequest.prototype.open = function (this: XMLHttpRequest, method: string, url: string | URL, ...rest: unknown[]) {
    blockedXhrUrls.set(this, `${String(method).toUpperCase()} ${String(url)}`);
    return (nativeOpen as (...args: unknown[]) => void).call(this, 'GET', 'about:blank', ...rest);
  } as typeof XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.send = function (this: XMLHttpRequest) {
    log('XHR', blockedXhrUrls.get(this) ?? 'unknown request', 'blocked');
    this.abort();
  };

  return () => {
    window.fetch = nativeFetch;
    if (originalSetSrc) Object.defineProperty(HTMLImageElement.prototype, 'src', originalSetSrc);
    if (originalMediaSrc) Object.defineProperty(HTMLMediaElement.prototype, 'src', originalMediaSrc);
    Element.prototype.setAttribute = originalSetAttribute;
    XMLHttpRequest.prototype.open = nativeOpen;
    XMLHttpRequest.prototype.send = nativeSend;
    for (const url of gridImages.values()) URL.revokeObjectURL(url);
  };
}

function DemoApp() {
  const fixture = useRef<FixtureState>(createFixtureState());
  const [Panel, setPanel] = useState<null | ComponentType<Record<string, unknown>>>(null);
  const [panelKey, setPanelKey] = useState(0);
  const [mediaGroups, setMediaGroups] = useState<DirectorPreviewGroup[]>(fixture.current.media);
  const [requestNotes, setRequestNotes] = useState<RequestNote[]>([]);
  const [boundaryReady, setBoundaryReady] = useState(false);
  const mediaRef = useRef<DirectorPreviewGroup[]>(fixture.current.media);

  const publishMedia = useCallback((next: FixtureMediaGroup[]) => {
    fixture.current.media = next;
    mediaRef.current = next;
    setMediaGroups(next);
  }, []);

  useEffect(() => {
    useCanvasStore.getState().hydrateCanvasDraft({
      nodes: [], edges: [],
      mutation: { userEditsSinceHydrate: 0, lastMutationSource: null, pendingClearIntent: false },
    });
    const demoOperation = {
      dispose() {},
      async run() { return { status: 'applied' as const }; },
      async resolveServer() { return { status: 'applied' as const }; },
    } as unknown as ExternalCanvasOperation;
    const unregisterRuntime = registerFreezoneCanvasRuntime(
      PROJECT,
      CANVAS,
      (remote, merge) => {
        const localStore = useCanvasStore.getState();
        const merged = merge
          ? merge(remote.nodes as CanvasNode[], remote.edges as CanvasEdge[], localStore.nodes, localStore.edges)
          : { nodes: remote.nodes as CanvasNode[], edges: remote.edges as CanvasEdge[] };
        localStore.setCanvasData(merged.nodes, merged.edges);
        fixture.current.canvas = { ...remote, nodes: merged.nodes, edges: merged.edges };
        return true;
      },
      async () => true,
      undefined,
      undefined,
      demoOperation,
      null,
      undefined,
      () => clone(fixture.current.canvas),
    );
    const restoreBoundary = installMockBoundary(fixture.current, note => {
      setRequestNotes(notes => [note, ...notes].slice(0, 12));
    }, publishMedia);
    setBoundaryReady(true);
    let cancelled = false;
    void import('../features/canvas/production/DirectorProductionPanel').then(module => {
      if (!cancelled) setPanel(() => module.DirectorProductionPanel as unknown as ComponentType<Record<string, unknown>>);
    });
    return () => {
      cancelled = true;
      restoreBoundary();
      unregisterRuntime();
    };
  }, []);

  const refreshMedia = useCallback(async () => {
    const state = fixture.current;
    const pending = state.pendingVoiceTasks;
    const completed = pending.filter(task => task.refreshes >= 1);
    const remaining = pending.filter(task => task.refreshes < 1).map(task => ({ ...task, refreshes: task.refreshes + 1 }));
    state.pendingVoiceTasks = remaining;
    for (const task of completed) {
      const group = state.media.find(item => item.id === task.groupId);
      const video = group?.stages?.video;
      const spans = Array.isArray(video?.video_spans) ? video.video_spans : [];
      const nextSpans = spans.map((span, index) => index === task.spanIndex ? {
        ...span,
        external_audio_ready: true,
        external_audio_path: `/api/v1/projects/${PROJECT}/media/episodes/${EPISODE}/audio/${task.groupId}-dialogue.mp3`,
      } : span);
      if (video) updateMedia(state, task.groupId, { video: {
        ...video,
        video_spans: nextSpans,
        ambience_stem_path: `/api/v1/projects/${PROJECT}/media/episodes/${EPISODE}/audio/${task.groupId}-ambience.mp3`,
        ambience_stem_status: 'succeeded',
      } });
    }
    if (completed.length) publishMedia(state.media);
    return mediaRef.current;
  }, [publishMedia]);
  const resetDemo = () => {
    resetFixtureState(fixture.current);
    publishMedia(fixture.current.media);
    useCanvasStore.getState().hydrateCanvasDraft({
      nodes: [], edges: [],
      mutation: { userEditsSinceHydrate: 0, lastMutationSource: null, pendingClearIntent: false },
    });
    setPanelKey(key => key + 1);
    setRequestNotes([]);
  };

  const groups = GROUPS.map(group => ({
    id: group.id,
    title: group.title,
    shots: group.shots,
  }));
  const script = {
    document_id: 'demo-screenplay-document',
    revision_id: SCREENPLAY_ID,
    source_sync: { status: 'synced' },
    artifact: {
      status: 'ready',
      path: SCREENPLAY_PATH,
      sha256: SCREENPLAY_ID,
      source_revision: 1,
      matches_episode_source: true,
    },
  };
  return <main className="director-demo-page">
    <header className="director-demo-header">
      <a className="director-demo-brand" href="/" aria-label="Nuomi 首页"><span className="director-demo-mark">N</span><span>NUOMI<span className="director-demo-brand-sub">创作工作室</span></span></a>
      <nav className="director-demo-nav" aria-label="演示导航"><span>导演工作台</span><i>/</i><span>第 01 集</span><i>/</i><strong>制作流程</strong></nav>
      <span className="director-demo-badge"><span />交互演示</span>
    </header>

    <section className="director-demo-hero">
      <div className="director-demo-hero-copy">
        <p className="director-demo-eyebrow">DIRECTOR PRODUCTION · E01</p>
        <h1>先审分镜，<em>再放行生成。</em></h1>
        <p className="director-demo-lede">沿用导演制作面板验证从镜头表到成片的阶段门。剧本与资产以演示样例作为已就绪输入，接下来可以实际点击分镜审阅与批准。</p>
      </div>
      <div className="director-demo-hero-actions">
        <a className="director-demo-link" href="/studio-demo.html"><span aria-hidden="true">↗</span>打开 3D 调度演示</a>
        <button className="director-demo-reset" type="button" onClick={resetDemo}>重置演示</button>
      </div>
    </section>

    <section className="director-demo-safety" role="note" aria-label="演示范围">
      <span className="director-demo-safety-icon" aria-hidden="true">!</span>
      <div><strong>交互演示，无真实生成、配音或素材保存。</strong><p>屏幕中的状态、收据和节点均为浏览器内存中的本地模拟；审阅图片区是浏览器内存生成的 SVG 镜头草图，不是模型生成的分镜或视频。音频试听控件不加载媒体文件，所有未列入 fixture 的请求都会被拦截。</p></div>
      <span className="director-demo-boundary">{boundaryReady ? '本地请求边界已启用' : '正在启用本地边界'}</span>
    </section>

    <section className="director-demo-context" aria-label="当前演示上下文">
      <div><span>项目样例</span><strong>《山海拾遗》</strong></div>
      <div><span>集数 / 导演版本</span><strong>E01 · 白尾漏雨 · {REVISION}</strong></div>
      <div><span>角色 / 分镜组</span><strong>步知遥、朏朏、收简人 · 2 组 / 4 镜头</strong></div>
      <div><span>重置后的起始状态</span><strong>剧本、资产已就绪 → 分镜待生成</strong></div>
    </section>

    <section className="director-demo-workbench" aria-label="导演制作面板演示">
      <div className="director-demo-panel-heading"><div><p className="director-demo-eyebrow">REVIEW GATE</p><h2>整集制作流程</h2><p>先创建演示制作节点，再依次完成分镜、明确批准、视频、声音和整集合成。</p></div><span className="director-demo-local-tag">真实 UI · 本地 fixture</span></div>
      <div className="director-demo-panel-shell">
        {Panel ? <Panel
          key={panelKey}
          project={PROJECT}
          episode={EPISODE_NUMBER}
          revisionId={REVISION}
          active
          canvasId={CANVAS}
          groups={groups}
          script={script}
          mediaGroups={mediaGroups}
          onRefreshMedia={refreshMedia}
        /> : <div className="director-demo-loading">{boundaryReady ? '正在加载导演制作面板…' : '正在设置本地演示边界…'}</div>}
      </div>
    </section>

    <details className="director-demo-network" open={requestNotes.some(note => note.result === 'blocked')}>
      <summary><span>网络边界记录</span><span>{requestNotes.filter(note => note.result === 'blocked').length} 个请求已拦截 · {requestNotes.filter(note => note.result === 'mocked').length} 个 fixture 响应</span></summary>
      <p>只有 <code>{API_ROOT}</code> 下本演示明确列出的进度、画布、文档、运行与配音来源路由会返回内存数据。fetch 不会回落到真实网络；所有其他请求返回 403。分镜图片只允许两张当前页面创建的本地 SVG 示意。</p>
      <ul>{requestNotes.slice(0, 8).map((note, index) => <li key={`${note.method}:${note.path}:${index}`} className={note.result === 'blocked' ? 'is-blocked' : ''}><span>{note.result === 'blocked' ? '已拦截' : '本地模拟'}</span><code>{note.method} {note.path}</code></li>)}</ul>
      {requestNotes.length === 0 && <p className="director-demo-no-requests">面板尚未发出请求。</p>}
    </details>
    <footer className="director-demo-footer">Nuomi 导演制作面板 · 本演示仅验证交互顺序与审阅门，不代表生产任务执行结果。</footer>
  </main>;
}

const root = import.meta.hot?.data.root ?? createRoot(document.getElementById('root')!);
if (import.meta.hot) import.meta.hot.data.root = root;
root.render(<DemoApp />);
