import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';

const mocks = vi.hoisted(() => ({
  getDubbing: vi.fn(),
  prepare: vi.fn(),
  generate: vi.fn(),
  getWorkspace: vi.fn(),
  submitAudition: vi.fn(),
  awaitAudition: vi.fn(),
  fetchAuditionResult: vi.fn(),
}));

vi.mock('@/features/canvas/production/dubbingApi', () => ({
  dubbingApi: {
    get: mocks.getDubbing,
    prepare: mocks.prepare,
    generate: mocks.generate,
    retry: vi.fn(),
    adopt: vi.fn(),
  },
}));
vi.mock('@/features/canvas/production/directorWorkspaceApi', () => ({
  directorWorkspaceApi: { get: mocks.getWorkspace },
}));
vi.mock('@/api/ops', () => ({
  submitFreezoneAudioSpeech: mocks.submitAudition,
  fetchFreezoneJobResult: mocks.fetchAuditionResult,
}));
vi.mock('@/api/tasks', () => ({ awaitTaskCompletion: mocks.awaitAudition }));
vi.mock('@/lib/url-params', () => ({ readUrl: () => ({ project: 'project-1' }) }));

import { DubbingPanel } from '@/features/canvas/production/DubbingPanel';

const dialogue = {
  dialogue_id: 'dialogue-1',
  character_id: 'character-1',
  identity_id: 'identity-2',
  speaker: '林岚',
  script_revision: 'script-rev-3',
  source_revision: 'source-rev-5',
  group_id: 'group-1',
  text: '门外有人吗？',
  voice_version_id: 'voice-v1',
  target_duration_seconds: 2,
  performance: {},
  source_key: 'line-1',
  source_status: 'active' as const,
};

const preparation = {
  preparation_id: 'prep-1',
  episode: 1,
  group_id: 'group-1',
  script_revision: 'script-rev-3',
  source_revision: 'source-rev-5',
  expected_revision: 12,
  source_snapshot_hash: 'snapshot-hash',
  dialogue_ids: ['dialogue-1'],
  source_dialogue_ids: ['dialogue-1'],
  dialogues: [dialogue],
  source_dialogues: [dialogue],
  missing_voices: [],
  issues: [],
};

let state: Record<string, unknown>;

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined);
  useCanvasStore.getState().setCanvasData([
    { id: 'voice-1', type: CANVAS_NODE_TYPES.characterVoice, position: { x: 0, y: 0 }, data: {
      characterId: 'character-1', identityId: 'identity-2', voiceVersionId: 'voice-v1',
    } } as never,
    { id: 'dubbing-1', type: CANVAS_NODE_TYPES.audio, position: { x: 400, y: 0 }, data: {
      audioKind: 'speech', episodeNumber: 1, groupId: 'group-1',
    } } as never,
  ], [{ id: 'voice-to-dubbing', source: 'voice-1', target: 'dubbing-1' }] as never);

  state = {
    group_id: 'group-1',
    episode: 1,
    script_revision: 'script-rev-3',
    source_revision: 'source-rev-5',
    revision: 12,
    dialogues: [dialogue],
    preparation: null,
    tasks: [],
    takes: [],
    adoption: { revision: 0, by_dialogue: {} },
    impact: {
      stale_dialogue_ids: [], added_dialogue_ids: [], deleted_dialogue_ids: [],
      preserved_take_dialogue_ids: [], take_count: 0, adopted_take_dialogue_ids: [],
    },
    issues: [],
  };
  mocks.getDubbing.mockImplementation(async () => ({ ...state }));
  mocks.getWorkspace.mockResolvedValue({
    script: { document_id: 'screenplay-1', revision_id: 'script-rev-3' },
    groups: [{ id: 'group-1', objective: '档案室场景' }],
  });
  mocks.prepare.mockImplementation(async () => {
    state = { ...state, preparation };
    return preparation;
  });
  mocks.generate.mockResolvedValue({});
  mocks.submitAudition.mockResolvedValue({ task_key: 'audition-task', job_id: 'audition-job' });
  mocks.awaitAudition.mockResolvedValue(undefined);
  mocks.fetchAuditionResult.mockResolvedValue({ url: '/audition.wav' });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it('disables generation when a bound fixed voice version changes after preparation', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DubbingPanel nodeId="dubbing-1" />
    </QueryClientProvider>,
  );

  fireEvent.click(screen.getByRole('button', { name: '剧本配音与表演调整' }));
  await screen.findByText('门外有人吗？');
  fireEvent.click(screen.getByRole('button', { name: '准备对白与声音引用' }));
  await waitFor(() => expect(mocks.prepare).toHaveBeenCalledWith('project-1', 1, 'group-1', expect.objectContaining({
    script_revision: 'script-rev-3',
    voice_versions: [{ character_id: 'character-1', identity_id: 'identity-2', version_id: 'voice-v1' }],
  })));

  const generate = await screen.findByRole('button', { name: '生成配音候选' });
  await waitFor(() => expect(generate).toBeEnabled());

  act(() => useCanvasStore.getState().updateNodeData('voice-1', { voiceVersionId: 'voice-v2' }));
  await waitFor(() => expect(generate).toBeDisabled());
  expect(screen.getByRole('status')).toHaveTextContent('请重新准备后再生成');

  fireEvent.click(generate);
  expect(mocks.generate).not.toHaveBeenCalled();
});

it('offers ordered audition modes and reports a missing take instead of skipping it', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DubbingPanel nodeId="dubbing-1" />
    </QueryClientProvider>,
  );

  fireEvent.click(screen.getByRole('button', { name: '剧本配音与表演调整' }));
  await screen.findByText('门外有人吗？');

  fireEvent.click(screen.getByRole('button', { name: '试听整组' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(/dialogue-1.*缺少.*试听/);
  expect(screen.getByRole('button', { name: '试听单句' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '试听前后句' })).toBeInTheDocument();
});

it('does not replace edits made while a free audition is completing', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let finishTask!: () => void;
  mocks.awaitAudition.mockImplementation(() => new Promise<void>(resolve => { finishTask = resolve; }));
  render(
    <QueryClientProvider client={client}>
      <DubbingPanel nodeId="dubbing-1" />
    </QueryClientProvider>,
  );

  fireEvent.click(screen.getByRole('button', { name: '剧本配音与表演调整' }));
  await screen.findByText('门外有人吗？');
  fireEvent.change(screen.getByLabelText('自由试音文本'), { target: { value: '用于本次试音的原稿' } });
  fireEvent.click(screen.getByRole('button', { name: '自由试音' }));

  await waitFor(() => expect(mocks.awaitAudition).toHaveBeenCalledWith('audition-task', 'project-1'));
  const auditionInput = screen.getByLabelText('自由试音文本');
  fireEvent.change(auditionInput, { target: { value: '用户后来修改的新草稿' } });
  expect(useCanvasStore.getState().nodes.find(node => node.id === 'dubbing-1')?.data.auditionDraft)
    .toMatchObject({ text: '用户后来修改的新草稿' });

  await act(async () => { finishTask(); });
  await waitFor(() => expect(
    useCanvasStore.getState().nodes.find(node => node.id === 'dubbing-1')?.data.auditionResult,
  ).toMatchObject({ text: '用于本次试音的原稿', voiceVersionId: 'voice-v1', url: '/audition.wav' }));
  expect(useCanvasStore.getState().nodes.find(node => node.id === 'dubbing-1')?.data.auditionDraft)
    .toMatchObject({ text: '用户后来修改的新草稿' });
});
