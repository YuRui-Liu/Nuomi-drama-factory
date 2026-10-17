import { beforeEach, expect, it } from 'vitest';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import {
  connectVoiceNodeToAudio,
  findSelectedAdoptedVoiceVersion,
  type VoiceVersionRecord,
} from '@/features/canvas/production/voice/voiceNodeBinding';

const adopted: VoiceVersionRecord = {
  version_id: 'version-1',
  character_id: 'character-1',
  identity_id: 'identity-1',
  slot: 'default',
  candidate_id: 'candidate-1',
  audio_sha256: 'hash-1',
  path: '/voices/one.wav',
  url: '/voices/one.wav',
  current: true,
};

beforeEach(() => {
  useCanvasStore.getState().setCanvasData([], []);
});

it('does not resolve an audition candidate as an adopted voice version', () => {
  const nodeData = { characterId: 'character-1', voiceVersionId: 'candidate-2' };
  const auditionCandidate = { candidate_id: 'candidate-2', character_id: 'character-1', url: '/candidate.wav' };

  expect(findSelectedAdoptedVoiceVersion(nodeData, [auditionCandidate])).toBeNull();
});

it('creates and connects a fixed voice audio node without starting synthesis', () => {
  useCanvasStore.getState().setCanvasData([
    { id: 'voice-node-1', type: CANVAS_NODE_TYPES.characterVoice, position: { x: 10, y: 20 }, data: {
      characterId: 'character-1', voiceVersionId: 'version-1',
    } } as never,
  ], []);

  const audioNodeId = connectVoiceNodeToAudio({
    projectId: 'project-1',
    sourceNodeId: 'voice-node-1',
    characterName: '林岚',
    version: adopted,
  });

  const state = useCanvasStore.getState();
  const audioNode = state.nodes.find(node => node.id === audioNodeId);
  expect(audioNode).toMatchObject({
    type: CANVAS_NODE_TYPES.audio,
    data: {
      audioUrl: null,
      audioKind: 'speech',
      voiceVersionId: 'version-1',
      sourceVoiceNodeId: 'voice-node-1',
      voiceRef: {
        scope: 'character_voice_version',
        characterName: '林岚',
        characterId: 'character-1',
        identityId: 'identity-1',
        slot: 'default',
        versionId: 'version-1',
      },
    },
  });
  expect(state.edges).toEqual(expect.arrayContaining([
    expect.objectContaining({ source: 'voice-node-1', target: audioNodeId }),
  ]));
  expect(audioNode?.data.audioUrl).toBeNull();
});

it('reuses the same fixed binding but keeps old version outputs intact', () => {
  useCanvasStore.getState().setCanvasData([
    { id: 'voice-node-1', type: CANVAS_NODE_TYPES.characterVoice, position: { x: 10, y: 20 }, data: {
      characterId: 'character-1', voiceVersionId: 'version-1',
    } } as never,
  ], []);

  const firstId = connectVoiceNodeToAudio({
    projectId: 'project-1', sourceNodeId: 'voice-node-1', characterName: '林岚', version: adopted,
  });
  if (!firstId) throw new Error('Expected the adopted voice to create an audio node');
  useCanvasStore.getState().updateNodeData(firstId, { audioUrl: '/takes/old.wav' });
  useCanvasStore.getState().updateNodeData('voice-node-1', { voiceVersionId: 'version-2' });

  const secondId = connectVoiceNodeToAudio({
    projectId: 'project-1', sourceNodeId: 'voice-node-1', characterName: '林岚',
    version: { ...adopted, version_id: 'version-2', audio_sha256: 'hash-2' },
  });
  const reusedSecondId = connectVoiceNodeToAudio({
    projectId: 'project-1', sourceNodeId: 'voice-node-1', characterName: '林岚',
    version: { ...adopted, version_id: 'version-2', audio_sha256: 'hash-2' },
  });

  const state = useCanvasStore.getState();
  expect(secondId).not.toBe(firstId);
  expect(reusedSecondId).toBe(secondId);
  expect(state.nodes.filter(node => node.type === CANVAS_NODE_TYPES.audio)).toHaveLength(2);
  expect(state.nodes.find(node => node.id === firstId)?.data).toMatchObject({
    audioUrl: '/takes/old.wav',
    voiceRef: { versionId: 'version-1' },
  });
});
