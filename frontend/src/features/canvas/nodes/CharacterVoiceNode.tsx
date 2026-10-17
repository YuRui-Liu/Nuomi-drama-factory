import { memo } from 'react';
import type { NodeProps } from '@xyflow/react';
import { useQuery } from '@tanstack/react-query';
import { apiCall } from '@/api/client';
import { CharacterVoicePanel } from '@/components/assets/character-voice-panel';
import { useCharacters } from '@/lib/queries/characters';
import { readUrl } from '@/lib/url-params';
import { useCanvasStore } from '@/stores/canvasStore';
import type { CharacterVoiceDesign } from '@/types/character';
import { CANVAS_NODE_TYPES, type ProductionContentNodeData } from '../domain/canvasNodes';
import { ProductionNodeShell } from '../production/ProductionNodeShell';
import { CharacterVoiceWorkspace, type VoiceWorkspaceCharacter } from '../production/voice/CharacterVoiceWorkspace';
import {
  connectVoiceNodeToAudio,
  findSelectedAdoptedVoiceVersion,
  type VoiceVersionRecord,
} from '../production/voice/voiceNodeBinding';

interface VoiceVersionsResponse {
  character_id: string;
  versions: VoiceVersionRecord[];
  current: Array<{ identity_id: string; slot: string; version_id: string }>;
}

interface CharacterVoiceNodeDraft {
  voiceDesign?: CharacterVoiceDesign | null;
  voiceDescriptionDraft?: string;
  voiceDescription?: string;
  auditionText?: string;
  voicePerceivedAge?: string;
  voiceDesignPreview?: null;
}

type CharacterVoiceNodeDrafts = Record<string, CharacterVoiceNodeDraft>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function characterDraftKey(characterId: string | undefined, characterName: string): string {
  return characterId ? `id:${characterId}` : `name:${characterName}`;
}

function readCharacterDrafts(data: ProductionContentNodeData): CharacterVoiceNodeDrafts {
  return isRecord(data.voiceDraftsByCharacter)
    ? data.voiceDraftsByCharacter as CharacterVoiceNodeDrafts
    : {};
}

function draftFromNodeData(data: ProductionContentNodeData): CharacterVoiceNodeDraft {
  return {
    voiceDesign: data.voiceDesign as CharacterVoiceDesign | null | undefined,
    voiceDescriptionDraft: typeof data.voiceDescriptionDraft === 'string' ? data.voiceDescriptionDraft : undefined,
    voiceDescription: typeof data.voiceDescription === 'string' ? data.voiceDescription : undefined,
    auditionText: typeof data.auditionText === 'string' ? data.auditionText : undefined,
    voicePerceivedAge: typeof data.voicePerceivedAge === 'string' ? data.voicePerceivedAge : undefined,
    voiceDesignPreview: data.voiceDesignPreview === null ? null : undefined,
  };
}

export const CharacterVoiceNode = memo(function CharacterVoiceNode({ id, data }: NodeProps) {
  const d = data as ProductionContentNodeData;
  const project = readUrl().project ?? '';
  const update = useCanvasStore(state => state.updateNodeData);
  const characters = useCharacters(project);
  const characterRows = characters.data?.data ?? [];
  const workspaceCharacters: VoiceWorkspaceCharacter[] = characterRows.map(item => ({
    name: item.name,
    character_id: (item as unknown as { character_id?: string }).character_id,
  }));
  const character = characterRows.find(item => d.characterId
    ? (item as unknown as { character_id?: string }).character_id === d.characterId
    : item.name === d.characterName);
  const characterId = character
    ? (character as unknown as { character_id?: string }).character_id
    : undefined;
  const activeDraftKey = character ? characterDraftKey(characterId, character.name) : '';
  const characterDrafts = readCharacterDrafts(d);
  const activeDraft = activeDraftKey
    ? characterDrafts[activeDraftKey] ?? (isRecord(d.voiceDraftsByCharacter) ? undefined : draftFromNodeData(d))
    : undefined;
  const versions = useQuery({
    queryKey: ['character-voice-versions', project, character?.name],
    queryFn: () => apiCall<VoiceVersionsResponse>(
      `projects/${encodeURIComponent(project)}/characters/${encodeURIComponent(character!.name)}/voice-versions`,
    ),
    enabled: Boolean(project && character),
    refetchOnWindowFocus: true,
  });
  const adoptedVersions = versions.data?.versions ?? [];
  const selectedVersion = findSelectedAdoptedVoiceVersion(d, adoptedVersions);

  const isNodeContextCurrent = () => {
    if ((readUrl().project ?? '') !== project || !character) return false;
    const currentNode = useCanvasStore.getState().nodes.find(node => node.id === id);
    if (!currentNode || currentNode.type !== CANVAS_NODE_TYPES.characterVoice) return false;
    const currentData = currentNode.data as ProductionContentNodeData;
    return characterDraftKey(
      typeof currentData.characterId === 'string' ? currentData.characterId : undefined,
      typeof currentData.characterName === 'string' ? currentData.characterName : '',
    ) === activeDraftKey;
  };

  const updateCharacterVoiceDraft = (patch: CharacterVoiceNodeDraft) => {
    if (!isNodeContextCurrent()) return;
    const currentNode = useCanvasStore.getState().nodes.find(node => node.id === id);
    if (!currentNode) return;
    const currentData = currentNode.data as ProductionContentNodeData;
    const drafts = readCharacterDrafts(currentData);
    const currentDraft = drafts[activeDraftKey] ?? draftFromNodeData(currentData);
    update(id, {
      ...patch,
      voiceDraftsByCharacter: {
        ...drafts,
        [activeDraftKey]: { ...currentDraft, ...patch },
      },
    });
  };

  const selectCharacter = (name: string) => {
    const selected = characterRows.find(item => item.name === name);
    if (!selected || selected.name === character?.name) return;
    const drafts = readCharacterDrafts(d);
    const currentKey = character ? characterDraftKey(characterId, character.name) : '';
    const nextDrafts = currentKey
      ? { ...drafts, [currentKey]: drafts[currentKey] ?? draftFromNodeData(d) }
      : drafts;
    const selectedId = (selected as unknown as { character_id?: string }).character_id;
    const selectedDraft = nextDrafts[characterDraftKey(selectedId, selected.name)];
    update(id, {
      characterName: selected.name,
      characterId: selectedId ?? '',
      identityId: undefined,
      slot: undefined,
      voiceVersionId: undefined,
      voiceReference: undefined,
      voiceDesign: selectedDraft?.voiceDesign ?? null,
      voiceDescription: selectedDraft?.voiceDescription ?? '',
      voiceDescriptionDraft: selectedDraft?.voiceDescriptionDraft ?? '',
      auditionText: selectedDraft?.auditionText ?? '',
      voicePerceivedAge: selectedDraft?.voicePerceivedAge ?? '',
      voiceDesignPreview: null,
      referenceOnly: false,
      voiceDraftsByCharacter: nextDrafts,
    });
  };

  const bindVersion = (version: VoiceVersionRecord) => update(id, {
    referenceOnly: true,
    characterId: version.character_id,
    identityId: version.identity_id,
    slot: version.slot,
    voiceVersionId: version.version_id,
    voiceReference: {
      character_id: version.character_id,
      identity_id: version.identity_id,
      slot: version.slot,
      version_id: version.version_id,
      audio_sha256: version.audio_sha256,
    },
  });

  const connectToAudio = () => {
    if (!character || !selectedVersion) return false;
    return Boolean(connectVoiceNodeToAudio({
      projectId: project,
      sourceNodeId: id,
      characterName: character.name,
      version: selectedVersion,
    }));
  };

  const summary = selectedVersion
    ? `${character?.name ?? '角色'} · ${selectedVersion.slot} · 已固定声音版本 ${selectedVersion.version_id}`
    : '设计音色、导入参考或复用声音；先试听并采用，再固定版本供配音节点使用';
  const versionError = versions.error ? String(versions.error) : null;

  return <ProductionNodeShell
    title="角色声音"
    summary={summary}
    status={selectedVersion ? `已固定采用版本 ${selectedVersion.version_id}` : '试听候选与已采用版本分开管理'}
    actionLabel="打开角色声音工作区"
    workspace
  >
    {controls => <CharacterVoiceWorkspace
      project={project}
      characters={workspaceCharacters}
      characterName={character?.name ?? ''}
      boundVersionId={typeof d.voiceVersionId === 'string' ? d.voiceVersionId : undefined}
      versions={adoptedVersions}
      selectedVersion={selectedVersion}
      versionsLoading={versions.isLoading}
      versionsError={versionError}
      charactersError={characters.error ? String(characters.error) : null}
      onBack={controls.requestClose}
      onSelectCharacter={selectCharacter}
      onBindVersion={bindVersion}
      onRefreshVersions={() => void versions.refetch()}
      onConnect={connectToAudio}
    >
      {character ? <CharacterVoicePanel
        project={project}
        character={character}
        active
        nodeVoiceDesign={activeDraft?.voiceDesign}
        nodeVoiceDescriptionDraft={activeDraft?.voiceDescriptionDraft}
        nodeVoiceDescription={activeDraft?.voiceDescription}
        nodeAuditionText={activeDraft?.auditionText}
        nodeVoicePerceivedAge={activeDraft?.voicePerceivedAge}
        isNodeContextCurrent={isNodeContextCurrent}
        onNodeDataChange={updateCharacterVoiceDraft}
      /> : null}
    </CharacterVoiceWorkspace>}
  </ProductionNodeShell>;
});
