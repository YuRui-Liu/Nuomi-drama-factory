import { CANVAS_NODE_TYPES, type AudioVoiceRef } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';

export interface VoiceVersionRecord {
  version_id: string;
  character_id: string;
  identity_id: string;
  slot: string;
  candidate_id: string;
  audio_sha256: string;
  path: string;
  url: string;
  current: boolean;
}

type VoiceNodeSelection = {
  characterId?: unknown;
  voiceVersionId?: unknown;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isVoiceVersionRecord(value: unknown): value is VoiceVersionRecord {
  if (!isRecord(value)) return false;
  return typeof value.version_id === 'string' && value.version_id.trim() !== ''
    && typeof value.character_id === 'string' && value.character_id.trim() !== ''
    && typeof value.slot === 'string' && value.slot.trim() !== ''
    && typeof value.audio_sha256 === 'string' && value.audio_sha256.trim() !== ''
    && typeof value.identity_id === 'string';
}

/** Resolve only a row from the adopted voice-version endpoint, never a preview candidate. */
export function findSelectedAdoptedVoiceVersion(
  nodeData: VoiceNodeSelection,
  versions: readonly unknown[] | undefined,
): VoiceVersionRecord | null {
  const characterId = typeof nodeData.characterId === 'string' ? nodeData.characterId : '';
  const versionId = typeof nodeData.voiceVersionId === 'string' ? nodeData.voiceVersionId : '';
  if (!characterId || !versionId || !versions) return null;
  return versions.find((item): item is VoiceVersionRecord =>
    isVoiceVersionRecord(item)
    && item.character_id === characterId
    && item.version_id === versionId,
  ) ?? null;
}

export function toFixedAudioVoiceRef(
  version: VoiceVersionRecord,
  characterName: string,
): AudioVoiceRef {
  return {
    scope: 'character_voice_version',
    characterName,
    characterId: version.character_id,
    identityId: version.identity_id || undefined,
    slot: version.slot,
    versionId: version.version_id,
  };
}

export function voiceNodeBindingKey(
  projectId: string,
  sourceNodeId: string,
  version: VoiceVersionRecord,
): string {
  return [projectId, sourceNodeId, version.character_id, version.version_id].join(':');
}

interface ConnectVoiceNodeInput {
  projectId: string;
  sourceNodeId: string;
  characterName: string;
  version: VoiceVersionRecord;
}

/** Create or reuse an empty audio node for a fixed adopted version. This only changes the canvas graph. */
export function connectVoiceNodeToAudio(
  input: ConnectVoiceNodeInput,
  store = useCanvasStore.getState(),
): string | null {
  const source = store.nodes.find(node => node.id === input.sourceNodeId);
  if (
    !source
    || source.type !== CANVAS_NODE_TYPES.characterVoice
    || source.data.characterId !== input.version.character_id
    || source.data.voiceVersionId !== input.version.version_id
  ) {
    return null;
  }

  const bindingKey = voiceNodeBindingKey(input.projectId, input.sourceNodeId, input.version);
  const voiceRef = toFixedAudioVoiceRef(input.version, input.characterName);
  const existing = store.nodes.find(node =>
    node.type === CANVAS_NODE_TYPES.audio && node.data.voiceBindingKey === bindingKey,
  );
  if (existing) {
    if (!store.edges.some(edge => edge.source === source.id && edge.target === existing.id)) {
      if (!store.addEdge(source.id, existing.id)) return null;
    }
    store.setSelectedNode(existing.id);
    store.requestFocusNode(existing.id);
    return existing.id;
  }

  const position = store.findNodePosition(source.id, 380, 320);
  const audioNodeId = store.addNode(CANVAS_NODE_TYPES.audio, position, {
    displayName: `${input.characterName} · 配音`,
    audioUrl: null,
    audioKind: 'speech',
    voiceRef,
    voiceLabel: `${input.characterName} · ${input.version.slot} · ${input.version.version_id}`,
    voiceVersionId: input.version.version_id,
    sourceVoiceNodeId: source.id,
    projectId: input.projectId,
    voiceBindingKey: bindingKey,
  });
  if (!store.addEdge(source.id, audioNodeId)) return null;
  store.setSelectedNode(audioNodeId);
  store.requestFocusNode(audioNodeId);
  return audioNodeId;
}
