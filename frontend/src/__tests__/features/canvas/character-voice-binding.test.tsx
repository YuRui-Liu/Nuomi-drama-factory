import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReactFlowProvider } from '@xyflow/react';
import type { NodeProps } from '@xyflow/react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';

const mocks = vi.hoisted(() => ({ apiCall: vi.fn(), useCharacters: vi.fn(), project: 'project-1' }));

vi.mock('@/api/client', () => ({ apiCall: mocks.apiCall }));
vi.mock('@/lib/queries/characters', () => ({ useCharacters: mocks.useCharacters }));
vi.mock('@/lib/url-params', () => ({ readUrl: () => ({ project: mocks.project }) }));
vi.mock('@/components/assets/character-voice-panel', () => ({
  CharacterVoicePanel: ({
    character,
    nodeVoiceDescriptionDraft,
    nodeAuditionText,
    onNodeDataChange,
  }: {
    character: { name: string };
    nodeVoiceDescriptionDraft?: string;
    nodeAuditionText?: string;
    onNodeDataChange?: (patch: Record<string, unknown>) => void;
  }) => <div data-testid={`voice-panel-${character.name}`}>
    <textarea aria-label="角色声音描述草案" value={nodeVoiceDescriptionDraft ?? ''} onChange={event => onNodeDataChange?.({ voiceDescriptionDraft: event.target.value })} />
    <textarea aria-label="角色试听文本" value={nodeAuditionText ?? ''} onChange={event => onNodeDataChange?.({ auditionText: event.target.value })} />
  </div>,
}));
vi.mock('@/features/canvas/production/ProductionNodeShell', () => ({
  ProductionNodeShell: ({ children }: { children?: ReactNode | ((controls: { requestClose: () => void; forceClose: () => void }) => ReactNode) }) => <div>
    {typeof children === 'function' ? children({ requestClose: () => undefined, forceClose: () => undefined }) : children}
  </div>,
}));

import { CharacterVoiceNode } from '@/features/canvas/nodes/CharacterVoiceNode';

function CanvasBoundCharacterVoiceNode({ id }: { id: string }) {
  const data = useCanvasStore(state => state.nodes.find(node => node.id === id)?.data);
  return data ? <CharacterVoiceNode {...({ id, data } as unknown as NodeProps)} /> : null;
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.project = 'project-1';
  useCanvasStore.getState().setCanvasData([], []);
  mocks.useCharacters.mockReturnValue({
    data: { data: [{ name: '林岚', character_id: 'character-1' }] },
    error: null,
  });
  mocks.apiCall.mockResolvedValue({
    character_id: 'character-1',
    versions: [{
      version_id: 'voice-version-7',
      character_id: 'character-1',
      identity_id: 'identity-2',
      slot: 'dialogue',
      candidate_id: 'candidate-7',
      audio_sha256: 'sha256-fixed-audio',
      path: '/voice/version-7.wav',
      url: '',
      current: false,
    }],
    current: [],
  });
});

it('uses one action to bind a fixed voice version and open its audio node', async () => {
  const initialData = { characterId: 'character-1', characterName: '林岚' };
  useCanvasStore.getState().setCanvasData([
    { id: 'voice-1', type: CANVAS_NODE_TYPES.characterVoice, position: { x: 0, y: 0 }, data: initialData } as never,
  ], []);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const nodeProps = { id: 'voice-1', data: initialData } as unknown as NodeProps;

  render(
    <QueryClientProvider client={client}>
      <ReactFlowProvider>
        <CharacterVoiceNode {...nodeProps} />
      </ReactFlowProvider>
    </QueryClientProvider>,
  );

  await waitFor(() => expect(mocks.apiCall).toHaveBeenCalled());
  expect(mocks.apiCall).toHaveBeenCalledWith(expect.stringContaining('/voice-versions'));
  fireEvent.click(await screen.findByRole('button', { name: '使用此声音' }));

  const node = useCanvasStore.getState().nodes.find(item => item.id === 'voice-1');
  expect(node?.data).toMatchObject({
    referenceOnly: true,
    characterId: 'character-1',
    identityId: 'identity-2',
    voiceVersionId: 'voice-version-7',
    voiceReference: {
      character_id: 'character-1',
      identity_id: 'identity-2',
      slot: 'dialogue',
      version_id: 'voice-version-7',
      audio_sha256: 'sha256-fixed-audio',
    },
  });
  const audioNode = useCanvasStore.getState().nodes.find(item => item.type === CANVAS_NODE_TYPES.audio);
  expect(audioNode?.data).toMatchObject({
    audioUrl: null,
    voiceRef: { scope: 'character_voice_version', characterId: 'character-1', versionId: 'voice-version-7' },
  });
  expect(useCanvasStore.getState().edges).toEqual(expect.arrayContaining([
    expect.objectContaining({ source: 'voice-1', target: audioNode?.id }),
  ]));
});

it('does not show an adopted voice response from the previous project after switching projects', async () => {
  let releasePreviousProject!: (value: unknown) => void;
  const previousProjectResponse = new Promise<unknown>(resolve => { releasePreviousProject = resolve; });
  const version = (versionId: string, characterId: string) => ({
    version_id: versionId,
    character_id: characterId,
    identity_id: 'identity-2',
    slot: 'dialogue',
    candidate_id: `candidate-${versionId}`,
    audio_sha256: `sha256-${versionId}`,
    path: `/${versionId}.wav`,
    url: '',
    current: true,
  });
  mocks.apiCall.mockImplementation((path: string) => path.includes('/projects/project-1/')
    ? previousProjectResponse
    : Promise.resolve({ character_id: 'character-1', versions: [version('project-2-version', 'character-1')], current: [] }));

  const initialData = { characterId: 'character-1', characterName: '林岚', voiceVersionId: 'removed-version' };
  useCanvasStore.getState().setCanvasData([
    { id: 'voice-1', type: CANVAS_NODE_TYPES.characterVoice, position: { x: 0, y: 0 }, data: initialData } as never,
  ], []);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const nodeProps = { id: 'voice-1', data: initialData } as unknown as NodeProps;
  const view = render(
    <QueryClientProvider client={client}>
      <ReactFlowProvider><CharacterVoiceNode {...nodeProps} /></ReactFlowProvider>
    </QueryClientProvider>,
  );

  await waitFor(() => expect(mocks.apiCall).toHaveBeenCalledWith(expect.stringContaining('projects/project-1/')));
  mocks.project = 'project-2';
  view.rerender(
    <QueryClientProvider client={client}>
      <ReactFlowProvider><CharacterVoiceNode {...nodeProps} /></ReactFlowProvider>
    </QueryClientProvider>,
  );

  const adoptedVersions = screen.getByRole('region', { name: '可用角色声音' });
  expect(await within(adoptedVersions).findByText('dialogue · 当前声音')).toBeInTheDocument();
  fireEvent.click(within(adoptedVersions).getByRole('button', { name: '使用此声音' }));
  const audioNode = useCanvasStore.getState().nodes.find(item => item.type === CANVAS_NODE_TYPES.audio);
  expect(audioNode?.data.voiceRef).toMatchObject({ versionId: 'project-2-version' });

  await act(async () => {
    releasePreviousProject({ character_id: 'character-1', versions: [version('project-1-version', 'character-1')], current: [] });
  });
  expect(useCanvasStore.getState().nodes.find(item => item.type === CANVAS_NODE_TYPES.audio)?.data.voiceRef)
    .toMatchObject({ versionId: 'project-2-version' });
  expect(within(adoptedVersions).queryByText(/project-1-version/)).not.toBeInTheDocument();
});

it('restores each role voice draft after switching roles and reopening the workspace', async () => {
  mocks.useCharacters.mockReturnValue({
    data: { data: [
      { name: '林岚', character_id: 'character-1' },
      { name: '站台陌生人', character_id: 'character-2' },
    ] },
    error: null,
  });
  const initialData = { characterId: 'character-1', characterName: '林岚' };
  useCanvasStore.getState().setCanvasData([
    { id: 'voice-1', type: CANVAS_NODE_TYPES.characterVoice, position: { x: 0, y: 0 }, data: initialData } as never,
  ], []);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const renderNode = () => render(
    <QueryClientProvider client={client}>
      <ReactFlowProvider><CanvasBoundCharacterVoiceNode id="voice-1" /></ReactFlowProvider>
    </QueryClientProvider>,
  );

  const view = renderNode();
  const description = await screen.findByRole('textbox', { name: '角色声音描述草案' });
  const audition = screen.getByRole('textbox', { name: '角色试听文本' });
  fireEvent.change(description, { target: { value: 'A 的自由声音描述' } });
  fireEvent.change(audition, { target: { value: 'A 的试听台词' } });
  expect(useCanvasStore.getState().nodes.find(node => node.id === 'voice-1')?.data).toMatchObject({
    voiceDraftsByCharacter: {
      'id:character-1': {
        voiceDescriptionDraft: 'A 的自由声音描述',
        auditionText: 'A 的试听台词',
      },
    },
  });

  fireEvent.click(screen.getByRole('button', { name: '站台陌生人' }));
  expect(await screen.findByRole('textbox', { name: '角色声音描述草案' })).toHaveValue('');
  expect(screen.getByRole('textbox', { name: '角色试听文本' })).toHaveValue('');
  fireEvent.click(screen.getByRole('button', { name: '林岚' }));
  expect(await screen.findByRole('textbox', { name: '角色声音描述草案' })).toHaveValue('A 的自由声音描述');
  expect(screen.getByRole('textbox', { name: '角色试听文本' })).toHaveValue('A 的试听台词');

  view.unmount();
  renderNode();
  expect(await screen.findByRole('textbox', { name: '角色声音描述草案' })).toHaveValue('A 的自由声音描述');
  expect(screen.getByRole('textbox', { name: '角色试听文本' })).toHaveValue('A 的试听台词');
});
