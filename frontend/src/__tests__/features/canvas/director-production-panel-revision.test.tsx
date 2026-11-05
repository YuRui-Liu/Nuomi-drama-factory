import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from '@/api/client';
import { BackendStatusError } from '@/lib/api-errors';
import { DirectorProductionPanel } from '@/features/canvas/production/DirectorProductionPanel';

const mocks = vi.hoisted(() => ({
  createProductionGraph: vi.fn(),
  startCanvasRun: vi.fn(),
  progress: {
    get: vi.fn(), configure: vi.fn(), complete: vi.fn(), approve: vi.fn(),
  },
  changeDialogueSource: vi.fn(),
  readCanvasDocument: vi.fn(),
  writeCanvasDocument: vi.fn(),
  urlCanvas: 'canvas-a' as string | null,
  runtimeScope: null as { project: string; canvasId: string } | null,
  lastError: '',
  perform: vi.fn(async (_key: string, action: (requestId: string) => Promise<void>) => {
    try { await action('stable-request'); } catch (error) { mocks.lastError = String(error); }
  }),
  store: {
    nodes: [] as Array<{ id: string; type: string; data: Record<string, unknown> }>,
    edges: [] as Array<{ source: string; target: string; data?: Record<string, unknown> }>,
    addNode: vi.fn(() => 'voice-node'),
    addEdgeWithData: vi.fn(() => 'voice-edge'),
    findNodePosition: vi.fn(() => ({ x: 420, y: 320 })),
    setSelectedNode: vi.fn(),
    requestFocusNode: vi.fn(),
  },
}));

vi.mock('@/features/canvas/infrastructure/orchestrationGateway', () => ({
  createProductionGraph: mocks.createProductionGraph,
  startCanvasRun: mocks.startCanvasRun,
}));
vi.mock('@/features/canvas/production/productionProgressApi', () => ({
  productionProgressApi: mocks.progress,
}));
vi.mock('@/features/canvas/production/directorProductionAudioApi', () => ({
  changeDirectorGroupDialogueSource: mocks.changeDialogueSource,
}));
vi.mock('@/api/canvas', () => ({
  readCanvasDocument: mocks.readCanvasDocument,
  writeCanvasDocument: mocks.writeCanvasDocument,
}));
vi.mock('@/features/canvas/production/useProductionAction', () => ({
  useProductionAction: () => ({ busy: false, error: '', perform: mocks.perform }),
}));
vi.mock('@/lib/url-params', () => ({ readUrl: () => ({ project: 'project', canvas: mocks.urlCanvas }) }));
vi.mock('@/features/freezone/canvasSyncRuntime', () => ({
  currentFreezoneCanvasScope: () => mocks.runtimeScope,
}));
vi.mock('@/stores/canvasStore', () => ({
  useCanvasStore: Object.assign((select: (state: typeof mocks.store) => unknown) => select(mocks.store), {
    getState: () => mocks.store,
  }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.urlCanvas = 'canvas-a';
  mocks.runtimeScope = null;
  mocks.store.nodes = [];
  mocks.store.edges = [];
  mocks.lastError = '';
  mocks.changeDialogueSource.mockResolvedValue({ ok: true, data: { task_id: 'audio-task-1' } });
  const initialProgress = {
    episode_id: '003', mode: 'review', stop_after: 'storyboard', blocked: null,
    stages: Object.fromEntries(['screenplay', 'assets', 'storyboard', 'video', 'audio', 'composition'].map(stage => [stage, {
      status: 'pending', input_hash: null, outputs: {}, output_hash: null,
      approval_hash: null, approved_hash: null, review_resolution: null, error: null,
    }])) as Record<string, Record<string, unknown>>,
  };
  mocks.progress.get.mockResolvedValue(initialProgress);
  mocks.progress.configure.mockResolvedValue(initialProgress);
  mocks.progress.complete.mockImplementation(async (_project, _canvas, _episode, body) => ({
    ...initialProgress,
    stages: { ...initialProgress.stages, [body.stage]: {
      ...initialProgress.stages[body.stage], status: 'awaiting_review',
      input_hash: body.input_hash, outputs: body.outputs, approval_hash: 'screenplay-approval',
    } },
  }));
  mocks.progress.approve.mockImplementation(async (_project, _canvas, _episode, body) => ({
    ...initialProgress,
    stages: { ...initialProgress.stages, [body.stage]: {
      ...initialProgress.stages[body.stage], status: 'ready', approval_hash: body.input_hash,
      approved_hash: body.input_hash,
    } },
  }));
  mocks.readCanvasDocument.mockResolvedValue({ path: 'episodes/003/storyboard.md', text: '# storyboard', hash: 'table-sha' });
  mocks.perform.mockImplementation(async (_key, action) => {
    try { await action('stable-request'); } catch (error) { mocks.lastError = String(error); }
  });
  mocks.createProductionGraph.mockResolvedValue({
    target: 'production-target',
    node_ids: ['render-a', 'video-a', 'production-target'],
    submitted: false,
    canvas: { revision: 8, nodes: [], edges: [] },
  });
  mocks.createProductionGraph.mockImplementation(async () => {
    mocks.store.nodes = [
      { id: 'render-a', type: 'narrativeGroupStoryboardNode', data: { operation: 'render' } },
      { id: 'video-a', type: 'narrativeGroupVideoNode', data: { operation: 'video' } },
      { id: 'production-target', type: 'episodeComposeNode', data: { operation: 'compose' } },
    ];
    return {
      target: 'production-target',
      node_ids: ['render-a', 'video-a', 'production-target'],
      submitted: false,
      canvas: { revision: 8, nodes: [], edges: [] },
    };
  });
});
afterEach(() => vi.restoreAllMocks());

function storyboardReviewProgress() {
  const stage = (status: string, outputs: Record<string, unknown> = {}) => ({
    status, input_hash: null, outputs, output_hash: null,
    approval_hash: null, approved_hash: null, review_resolution: null, error: null,
  });
  return {
    episode_id: '003', mode: 'review', stop_after: 'storyboard', blocked: null,
    stages: {
      screenplay: stage('ready', { script: { path: 'episodes/003/script-receipt.md', sha256: 'a'.repeat(64) } }),
      assets: stage('ready', { reuse_checklist: { path: 'episodes/003/assets.md', sha256: 'b'.repeat(64) } }),
      storyboard: stage('pending'), video: stage('pending'), audio: stage('pending'), composition: stage('pending'),
    },
  };
}

const storyboardReceiptScript = { source_sync: { status: 'synced' }, artifact: {
  status: 'ready', path: 'episodes/003/script-receipt.md', sha256: 'a'.repeat(64), matches_episode_source: true,
} };

const storyboardReceiptMedia = [{ id: 'group-a', director_revision_id: 'revision-new', stages: { render: {
  status: 'completed', grid_asset: '/api/v1/projects/project/media/episodes/003/grid.png', needs_regeneration: false,
} } }];

const reviewGroups = (description: string) => [{
  id: 'group-a', title: '门口对峙', scene_anchor: '旧宅门口',
  objective: '让守门人拒绝女主进入', visible_turn: '门内传来熟悉的暗号',
  relation_to_previous: '承接上一组的追问',
  shots: [{
    id: 'shot-a', subject: '林遥', description, visual_description: '雨水沿着门缝流下',
    action: '林遥把沾血的信封推入门缝', space_anchor: '门槛外侧',
    shot_size: '近景', camera_angle: '低机位', camera_motion: '缓慢推进',
    composition: '门缝占据画面右侧', technique_intent: '压缩人物与出口距离',
    visible_start_state: '信封还在林遥手中', visible_end_state: '信封进入门内',
    duration_seconds: 4, dialogue_source_ids: ['line-1'],
    intent: { narrative_purpose: '用动作逼出守门人的回应', audience_attention: '门缝内的反应',
      emotional_effect: '制造被拒后仍坚持的紧张感', continuity_strategy: '保持门内外轴线' },
  }],
}];

const storyboardReceiptSemantics = { revision_id: 'semantic-1', status: 'active', can_activate: true,
  is_current_source: true, validation_report: { passed: true, issues: [] },
  scenes: [{ id: 'scene-1', heading: '旧宅门口', location: '旧宅', time_of_day: '夜', characters: ['林遥'],
    blocks: [{ id: 'line-1', text: '你说过，门会为我开。' }] }], beats: [],
};

it('creates the fixed-revision production graph and selects storyboard render for review without running it', async () => {
  render(<DirectorProductionPanel
    project="project" episode={3} revisionId="revision-new" active
    groups={[{ id: 'group-a', title: '门口对峙' }, { id: 'group-b', title: '雨夜追逐' }]}
  />);

  fireEvent.click(screen.getByRole('button', { name: '创建制作节点' }));

  await waitFor(() => expect(mocks.createProductionGraph).toHaveBeenCalledWith(
    'project', 'canvas-a', 3, 'revision-new', 'stable-request',
  ));
  expect(mocks.store.setSelectedNode).toHaveBeenCalledWith('render-a');
  expect(mocks.store.requestFocusNode).toHaveBeenCalledWith('render-a');
  expect(screen.getAllByRole('status')[0]).toHaveTextContent('render-a');
  expect(screen.getByText(/生成任务尚未提交/)).toBeInTheDocument();
  expect(mocks.startCanvasRun).not.toHaveBeenCalled();
});

it('requires an active adopted revision and does not select a target when safe graph reconciliation fails', async () => {
  const { rerender } = render(<DirectorProductionPanel
    project="project" episode={3} revisionId="candidate" active={false}
    groups={[{ id: 'group-a', title: '门口对峙' }]}
  />);
  expect(screen.getByRole('button', { name: '创建制作节点' })).toBeDisabled();
  expect(mocks.createProductionGraph).not.toHaveBeenCalled();

  mocks.createProductionGraph.mockRejectedValueOnce(new Error('制作节点已创建，但与正在编辑的画布内容有冲突'));
  rerender(<DirectorProductionPanel
    project="project" episode={3} revisionId="revision-new" active
    groups={[{ id: 'group-a', title: '门口对峙' }]}
  />);
  fireEvent.click(screen.getByRole('button', { name: '创建制作节点' }));

  await waitFor(() => expect(mocks.lastError).toContain('画布'));
  expect(mocks.store.setSelectedNode).not.toHaveBeenCalled();
  expect(mocks.startCanvasRun).not.toHaveBeenCalled();
});

it('uses the workspace canvas id when the route has no legacy canvas parameter', async () => {
  mocks.urlCanvas = null;
  window.history.replaceState({}, '', '/projects/01M55D4506KC3B2WSPSHCX9JBT/freezone?workspace=1afe547a80a04e4a96b731680213ab62');

  render(<DirectorProductionPanel
    project="project" episode={3} revisionId="revision-new" active
    groups={[{ id: 'group-a', title: '门口对峙' }]}
  />);
  fireEvent.click(screen.getByRole('button', { name: '创建制作节点' }));

  await waitFor(() => expect(mocks.createProductionGraph).toHaveBeenCalledWith(
    'project', '1afe547a80a04e4a96b731680213ab62', 3, 'revision-new', 'stable-request',
  ));
});

it('registers a current handed-off screenplay for explicit review without approving it automatically', async () => {
  render(<DirectorProductionPanel
    project="project" episode={3} revisionId="revision-new" active
    groups={[]}
    script={{ document_id: 'doc-1', revision_id: 'doc-rev-4', source_sync: { status: 'synced' }, artifact: {
      status: 'ready', path: 'episodes/003/script-receipt.md', sha256: 'a'.repeat(64), source_revision: 9, matches_episode_source: true,
    } }}
  />);

  fireEvent.click(await screen.findByRole('button', { name: '登记正式剧本并进入审阅' }));

  await waitFor(() => expect(mocks.progress.configure).toHaveBeenCalledWith(
    'project', 'canvas-a', '003', { mode: 'review', stop_after: 'storyboard' },
  ));
  expect(mocks.progress.complete).toHaveBeenCalledWith('project', 'canvas-a', '003', {
    stage: 'screenplay', input_hash: 'a'.repeat(64),
    outputs: { script: { path: 'episodes/003/script-receipt.md', sha256: 'a'.repeat(64) } },
  });
  expect(mocks.progress.approve).not.toHaveBeenCalled();
  expect(await screen.findByRole('button', { name: '确认剧本已审阅' })).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: '确认剧本已审阅' }));
  await waitFor(() => expect(mocks.progress.approve).toHaveBeenCalledWith(
    'project', 'canvas-a', '003', { stage: 'screenplay', input_hash: 'screenplay-approval' },
  ));
});

it('keeps video continuation unavailable until the storyboard receipt is explicitly approved', async () => {
  const stage = (status: string, outputs: Record<string, unknown> = {}, approval_hash: string | null = null) => ({
    status, input_hash: 'input', outputs, output_hash: null, approval_hash,
    approved_hash: null, review_resolution: null, error: null,
  });
  const waiting = {
    episode_id: '003', mode: 'review', stop_after: 'storyboard', blocked: null,
    stages: {
      screenplay: stage('ready', { script: { path: 'episodes/003/script-receipt.md', sha256: 'a'.repeat(64) } }),
      assets: stage('ready', { reuse_checklist: { path: 'episodes/003/assets.md', sha256: 'b'.repeat(64) } }),
      storyboard: stage('awaiting_review', {
        table: { path: 'episodes/003/storyboard.md', sha256: 'c'.repeat(64) },
        grid: ['episodes/003/grid.png'],
      }, 'storyboard-approval'),
      video: stage('pending'), audio: stage('pending'), composition: stage('pending'),
    },
  };
  mocks.progress.get.mockResolvedValue(waiting);
  mocks.progress.approve.mockResolvedValue({
    ...waiting,
    stages: { ...waiting.stages, storyboard: { ...waiting.stages.storyboard, status: 'ready', approved_hash: 'storyboard-approval' } },
  });
  mocks.store.nodes = [{ id: 'video-a', type: 'narrativeGroupVideoNode', data: {
    operation: 'video', episodeNumber: 3, groupId: 'group-a', directorRevisionId: 'revision-new', projectId: 'project',
  } }];

  render(<DirectorProductionPanel
    project="project" episode={3} revisionId="revision-new" active
    groups={[{ id: 'group-a', title: '门口对峙' }]}
    script={{ document_id: 'doc-1', revision_id: 'doc-rev-4', source_sync: { status: 'synced' }, artifact: {
      status: 'ready', path: 'episodes/003/script-receipt.md', sha256: 'a'.repeat(64), matches_episode_source: true,
    } }}
    mediaGroups={[{ id: 'group-a', director_revision_id: 'revision-new', stages: {
      render: { status: 'completed', grid_asset: '/api/v1/projects/project/media/episodes/003/grid.png', needs_regeneration: false },
    } }]}
  />);

  expect(await screen.findByRole('button', { name: '批准完整分镜' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /明确继续批量视频/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '批准完整分镜' }));
  expect(await screen.findByRole('button', { name: /明确继续批量视频（1）/ })).toBeEnabled();
  expect(mocks.progress.approve).toHaveBeenCalledWith(
    'project', 'canvas-a', '003', { stage: 'storyboard', input_hash: 'storyboard-approval' },
  );
});

it.each([
  ['legacy ApiError', () => new ApiError('document not found', 404)],
  ['apiCall BackendStatusError', () => new BackendStatusError('document not found', 404, { detail: 'document not found' })],
])('creates a missing review document after a %s 404', async (_name, makeNotFound) => {
  const ready = {
    status: 'ready', input_hash: 'ready', outputs: {}, output_hash: null,
    approval_hash: null, approved_hash: null, review_resolution: null, error: null,
  };
  const scriptReceipt = { source_sync: { status: 'synced' }, artifact: {
    status: 'ready', path: 'episodes/003/script-receipt.md', sha256: 'a'.repeat(64), matches_episode_source: true,
  } };
  mocks.progress.get.mockResolvedValue({ episode_id: '003', mode: 'review', stop_after: 'storyboard', blocked: null,
    stages: { screenplay: { ...ready, outputs: { script: { path: scriptReceipt.artifact.path, sha256: scriptReceipt.artifact.sha256 } } },
      assets: ready, storyboard: { ...ready, status: 'pending' }, video: ready, audio: ready, composition: ready } });
  mocks.readCanvasDocument.mockRejectedValueOnce(makeNotFound());
  mocks.writeCanvasDocument.mockImplementation(async (_project, _canvas, document) => ({
    path: document.path, text: document.text, hash: 'table-sha',
  }));

  render(<DirectorProductionPanel project="project" episode={3} revisionId="revision-new" active
    groups={[{ id: 'group-a', title: '门口对峙', shots: [{ id: 'shot-a', subject: '门口争执', duration_seconds: 4 }] }]}
    script={scriptReceipt}
    mediaGroups={[{ id: 'group-a', director_revision_id: 'revision-new', stages: { render: {
      status: 'completed', grid_asset: '/api/v1/projects/project/media/episodes/003/grid.png', needs_regeneration: false,
    } } }]}
  />);

  fireEvent.click(await screen.findByRole('button', { name: '登记分镜表与全部宫格图，进入审阅' }));

  await waitFor(() => expect(mocks.writeCanvasDocument).toHaveBeenCalledTimes(1));
  const writtenTable = mocks.writeCanvasDocument.mock.calls[0][2];
  expect(writtenTable).toEqual(expect.objectContaining({
    path: expect.stringMatching(/^episodes\/003\/storyboard-revision-new-[a-f0-9]{64}\.md$/),
    text: expect.stringContaining('门口争执'),
    expected_hash: null,
  }));
  expect(mocks.progress.complete).toHaveBeenCalledWith('project', 'canvas-a', '003', expect.objectContaining({
    stage: 'storyboard', outputs: { table: { path: writtenTable.path, sha256: 'table-sha' }, grid: ['episodes/003/grid.png'] },
  }));
});

it('surfaces an accepted screenplay source conflict and refuses to treat its receipt as ready', async () => {
  render(<DirectorProductionPanel project="project" episode={3} revisionId="revision-new" active groups={[]}
    script={{ source_sync: { status: 'conflict' }, artifact: {
      status: 'conflict', path: 'episodes/003/script-receipt.md', sha256: 'a'.repeat(64), matches_episode_source: false,
    } }}
  />);

  expect(await screen.findByRole('status')).toHaveTextContent('剧本提案已接受，但与当前分集来源发生冲突');
  expect(screen.getByRole('button', { name: '登记正式剧本并进入审阅' })).toBeDisabled();
});

it('submits external dialogue generation per span and never treats the original H3 mix as dubbed audio', async () => {
  const ready = {
    status: 'ready', input_hash: 'ready', outputs: {}, output_hash: null,
    approval_hash: null, approved_hash: null, review_resolution: null, error: null,
  };
  const scriptReceipt = { source_sync: { status: 'synced' }, artifact: {
    status: 'ready', path: 'episodes/003/script-receipt.md', sha256: 'a'.repeat(64), matches_episode_source: true,
  } };
  const mediaGroups = [{ id: 'group-a', director_revision_id: 'revision-new', stages: { video: {
    status: 'completed', revision: 7,
    video_asset: '/api/v1/projects/project/media/episodes/003/group-a.mp4',
    original_audio_path: '/api/v1/projects/project/media/episodes/003/original.mp3',
    ambience_stem_path: '/api/v1/projects/project/media/episodes/003/ambience.mp3',
    ambience_stem_status: 'succeeded',
    video_spans: [{ span_index: 0, beat_numbers: [1], start_seconds: 0, end_seconds: 4,
      has_dialogue: true, dialogue_source: 'external_tts' as const, external_audio_ready: false }],
  } } }];
  mocks.progress.get.mockResolvedValue({ episode_id: '003', mode: 'review', stop_after: null, blocked: null,
    stages: { screenplay: { ...ready, outputs: { script: { path: scriptReceipt.artifact.path, sha256: scriptReceipt.artifact.sha256 } } },
      assets: ready, storyboard: ready, video: ready, audio: { ...ready, status: 'pending' }, composition: { ...ready, status: 'pending' } } });
  const refreshMedia = vi.fn(async () => mediaGroups);
  render(<DirectorProductionPanel project="project" episode={3} revisionId="revision-new" active
    workspaceNodeId="director-workspace" groups={[{ id: 'group-a', title: '门口对峙' }]}
    script={scriptReceipt}
    mediaGroups={mediaGroups}
    onRefreshMedia={refreshMedia}
  />);

  fireEvent.click(await screen.findByRole('button', { name: '生成角色配音' }));
  await waitFor(() => expect(mocks.changeDialogueSource).toHaveBeenCalledWith(
    'project', 3, 'group-a', 0, 'external_tts', 7,
  ));
  expect(screen.getByText(/已提交叙事组 group-a 的角色配音任务/)).toBeInTheDocument();
  expect(mocks.progress.complete).not.toHaveBeenCalledWith('project', 'canvas-a', '003', expect.objectContaining({ stage: 'audio' }));
  fireEvent.click(screen.getByRole('button', { name: '刷新声音状态' }));
  expect(refreshMedia).toHaveBeenCalled();
  expect(await screen.findByText(/已刷新对白与环境音状态/)).toBeInTheDocument();
});

it('opens the existing character voice workspace as a context-linked canvas node', async () => {
  const ready = {
    status: 'ready', input_hash: 'ready', outputs: {}, output_hash: null,
    approval_hash: null, approved_hash: null, review_resolution: null, error: null,
  };
  const scriptReceipt = { source_sync: { status: 'synced' }, artifact: {
    status: 'ready', path: 'episodes/003/script-receipt.md', sha256: 'a'.repeat(64), matches_episode_source: true,
  } };
  mocks.progress.get.mockResolvedValue({ episode_id: '003', mode: 'review', stop_after: null, blocked: null,
    stages: { screenplay: { ...ready, outputs: { script: { path: scriptReceipt.artifact.path, sha256: scriptReceipt.artifact.sha256 } } },
      assets: ready, storyboard: ready, video: ready, audio: { ...ready, status: 'pending' }, composition: { ...ready, status: 'pending' } } });
  mocks.store.nodes = [{ id: 'director-workspace', type: 'directorWorkspaceNode', data: {} }];
  render(<DirectorProductionPanel project="project" episode={3} revisionId="revision-new" active
    workspaceNodeId="director-workspace" groups={[{ id: 'group-a', title: '门口对峙' }]} script={scriptReceipt}
    mediaGroups={[{ id: 'group-a', director_revision_id: 'revision-new', stages: { video: {
      status: 'completed', video_asset: '/api/v1/projects/project/media/episodes/003/group-a.mp4', revision: 7,
      video_spans: [{ span_index: 0, has_dialogue: true, dialogue_source: 'external_tts', external_audio_ready: false }],
    } } }]}
  />);

  fireEvent.click(await screen.findByRole('button', { name: '打开角色声音设计、导入与试听' }));

  expect(mocks.store.addNode).toHaveBeenCalledWith('characterVoiceNode', { x: 420, y: 320 }, expect.objectContaining({
    projectId: 'project', episodeNumber: 3, displayName: 'E003 · 角色声音',
  }));
  expect(mocks.store.addEdgeWithData).toHaveBeenCalledWith('director-workspace', 'voice-node', { contextLink: true });
  expect(mocks.store.setSelectedNode).toHaveBeenCalledWith('voice-node');
  expect(mocks.store.requestFocusNode).toHaveBeenCalledWith('voice-node');
});

it('registers external dialogue and ambience only from actual media files', async () => {
  const ready = {
    status: 'ready', input_hash: 'ready', outputs: {}, output_hash: null,
    approval_hash: null, approved_hash: null, review_resolution: null, error: null,
  };
  mocks.progress.get.mockResolvedValue({
    episode_id: '003', mode: 'review', stop_after: null, blocked: null,
    stages: { screenplay: ready, assets: ready, storyboard: ready, video: ready,
      audio: { ...ready, status: 'pending' }, composition: { ...ready, status: 'pending' } },
  });
  mocks.progress.complete.mockImplementation(async (_project, _canvas, _episode, body) => ({
    episode_id: '003', mode: 'review', stop_after: null, blocked: null,
    stages: { screenplay: ready, assets: ready, storyboard: ready, video: ready,
      audio: { ...ready, status: 'ready', input_hash: body.input_hash, outputs: {
        audio: body.outputs.audio.map((path: string) => ({ path, sha256: 'c'.repeat(64) })),
        ambience: body.outputs.ambience.map((path: string) => ({ path, sha256: 'd'.repeat(64) })),
      } },
      composition: { ...ready, status: 'pending' } },
  }));
  const video = {
    status: 'completed', revision: 7,
    video_asset: '/api/v1/projects/project/media/episodes/003/group-a.mp4',
    original_audio_path: '/api/v1/projects/project/media/episodes/003/original.mp3',
    ambience_stem_path: '/api/v1/projects/project/media/episodes/003/ambience.mp3',
    ambience_stem_status: 'succeeded',
    video_spans: [{ span_index: 0, beat_numbers: [1], start_seconds: 0, end_seconds: 4,
      has_dialogue: true, dialogue_source: 'external_tts' as const, external_audio_ready: true,
      external_audio_path: '/api/v1/projects/project/media/episodes/003/dialogue.mp3' }],
  };
  render(<DirectorProductionPanel project="project" episode={3} revisionId="revision-new" active
    groups={[{ id: 'group-a', title: '门口对峙' }]}
    mediaGroups={[{ id: 'group-a', director_revision_id: 'revision-new', stages: { video } }]}
  />);

  fireEvent.click(await screen.findByRole('button', { name: '登记声音阶段' }));

  await waitFor(() => expect(mocks.progress.complete).toHaveBeenCalledWith('project', 'canvas-a', '003', {
    stage: 'audio', input_hash: expect.any(String),
    outputs: { audio: ['episodes/003/dialogue.mp3'], ambience: ['episodes/003/ambience.mp3'] },
  }));
  expect(mocks.progress.approve).not.toHaveBeenCalled();
  expect(await screen.findByText('声音阶段已登记当前实际输出。')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '运行整集合成' })).toBeEnabled();
});

it('saves distinct story, action, camera, dialogue-source, and group context in the storyboard review table', async () => {
  const progress = storyboardReviewProgress();
  mocks.progress.get.mockResolvedValue(progress);
  mocks.progress.configure.mockResolvedValue(progress);
  mocks.progress.complete.mockResolvedValue(progress);
  mocks.readCanvasDocument.mockRejectedValue(new BackendStatusError('document not found', 404, { detail: 'document not found' }));
  mocks.writeCanvasDocument.mockImplementation(async (_project, _canvas, document) => ({
    path: document.path, text: document.text, hash: 'table-sha',
  }));

  render(<DirectorProductionPanel project="project" episode={3} revisionId="revision-new" active
    groups={reviewGroups('林遥发现门后有人转动门锁，仍把信封推入门缝。')}
    script={storyboardReceiptScript} semantics={storyboardReceiptSemantics} mediaGroups={storyboardReceiptMedia}
  />);

  fireEvent.click(await screen.findByRole('button', { name: '登记分镜表与全部宫格图，进入审阅' }));

  await waitFor(() => expect(mocks.writeCanvasDocument).toHaveBeenCalledTimes(1));
  const savedText = mocks.writeCanvasDocument.mock.calls[0][2].text;
  for (const detail of [
    '让守门人拒绝女主进入', '门内传来熟悉的暗号', '承接上一组的追问',
    '林遥', '林遥发现门后有人转动门锁，仍把信封推入门缝。', '雨水沿着门缝流下',
    '林遥把沾血的信封推入门缝', '门缝占据画面右侧', '用动作逼出守门人的回应',
    'line-1：你说过，门会为我开。', '信封还在林遥手中', '信封进入门内', '4 秒',
  ]) expect(savedText).toContain(detail);
});

it('uses a content-addressed storyboard receipt path when the active revision content changes', async () => {
  const progress = storyboardReviewProgress();
  mocks.progress.get.mockResolvedValue(progress);
  mocks.progress.configure.mockResolvedValue(progress);
  mocks.progress.complete.mockResolvedValue(progress);
  mocks.readCanvasDocument.mockRejectedValue(new BackendStatusError('document not found', 404, { detail: 'document not found' }));
  mocks.writeCanvasDocument.mockImplementation(async (_project, _canvas, document) => ({
    path: document.path, text: document.text, hash: `hash-${mocks.writeCanvasDocument.mock.calls.length}`,
  }));
  const { rerender } = render(<DirectorProductionPanel project="project" episode={3} revisionId="revision-new" active
    groups={reviewGroups('林遥停下动作，先听门后的脚步声。')}
    script={storyboardReceiptScript} mediaGroups={storyboardReceiptMedia}
  />);

  fireEvent.click(await screen.findByRole('button', { name: '登记分镜表与全部宫格图，进入审阅' }));
  await waitFor(() => expect(mocks.writeCanvasDocument).toHaveBeenCalledTimes(1));
  const firstPath = mocks.writeCanvasDocument.mock.calls[0][2].path;

  rerender(<DirectorProductionPanel project="project" episode={3} revisionId="revision-new" active
    groups={reviewGroups('门后脚步停下后，林遥把信封推入门缝。')}
    script={storyboardReceiptScript} mediaGroups={storyboardReceiptMedia}
  />);
  fireEvent.click(await screen.findByRole('button', { name: '登记分镜表与全部宫格图，进入审阅' }));
  await waitFor(() => expect(mocks.writeCanvasDocument).toHaveBeenCalledTimes(2));
  const secondPath = mocks.writeCanvasDocument.mock.calls[1][2].path;

  expect(firstPath).not.toBe(secondPath);
  expect(firstPath).toMatch(/^episodes\/003\/storyboard-revision-new-[a-f0-9]{64}\.md$/);
  expect(secondPath).toMatch(/^episodes\/003\/storyboard-revision-new-[a-f0-9]{64}\.md$/);
  expect(mocks.progress.complete.mock.calls.map(call => call[3].outputs.table.path)).toEqual([firstPath, secondPath]);
});
