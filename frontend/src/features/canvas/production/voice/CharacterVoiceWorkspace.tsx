import type { ReactNode, SyntheticEvent } from 'react';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { ProductionWorkspaceLayout } from '@/features/canvas/production/workspace/ProductionWorkspaceLayout';
import type { VoiceVersionRecord } from './voiceNodeBinding';

export interface VoiceWorkspaceCharacter {
  name: string;
  character_id?: string;
}

interface CharacterVoiceWorkspaceProps {
  project: string;
  characters: readonly VoiceWorkspaceCharacter[];
  characterName: string;
  boundVersionId?: string;
  versions: readonly VoiceVersionRecord[];
  selectedVersion?: VoiceVersionRecord | null;
  versionsLoading?: boolean;
  versionsError?: string | null;
  charactersError?: string | null;
  onBack: () => void;
  onSelectCharacter: (name: string) => void;
  onUseVersion: (version: VoiceVersionRecord) => boolean | void;
  onRefreshVersions: () => void;
  children: ReactNode;
}

function pauseOtherWorkspaceAudio(event: SyntheticEvent<HTMLElement>) {
  const current = event.target;
  const root = event.currentTarget.closest('.production-workspace-layout') ?? document;
  root.querySelectorAll('audio').forEach(audio => {
    if (audio !== current) audio.pause();
  });
}

export function CharacterVoiceWorkspace({
  project,
  characters,
  characterName,
  boundVersionId,
  versions,
  selectedVersion,
  versionsLoading = false,
  versionsError,
  charactersError,
  onBack,
  onSelectCharacter,
  onUseVersion,
  onRefreshVersions,
  children,
}: CharacterVoiceWorkspaceProps) {
  const [connectionError, setConnectionError] = useState(false);
  const selectedCharacter = characters.find(character => character.name === characterName);
  const bindingStatus = selectedVersion
    ? `${characterName} · ${selectedVersion.slot} · 已选用`
    : '声音设定与试听';

  return <ProductionWorkspaceLayout
    title="角色声音"
    onBack={onBack}
    status={bindingStatus}
    navigation={<div className="space-y-5">
      <div>
        <p className="mb-2 text-xs text-muted-foreground">项目角色</p>
        <nav aria-label="角色选择" className="space-y-1">
          {characters.map(character => <button
            key={character.character_id ?? character.name}
            type="button"
            aria-current={character.name === characterName ? 'page' : undefined}
            onClick={() => onSelectCharacter(character.name)}
            className={`w-full rounded-md px-3 py-2 text-left text-sm ${character.name === characterName ? 'bg-primary/12 font-medium text-primary' : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground'}`}
          >{character.name}</button>)}
          {charactersError && <p role="alert" className="mt-2 text-xs text-destructive">角色读取失败：{charactersError}</p>}
          {!characters.length && <p className="rounded-md bg-muted/40 p-3 text-xs text-muted-foreground">此项目还没有可选角色。</p>}
        </nav>
      </div>
      {selectedCharacter && <section className="border-t pt-4">
        <p className="text-xs text-muted-foreground">本节点角色</p>
        <p className="mt-1 text-sm font-medium">{selectedCharacter.name}</p>
        <p className="mt-2 text-xs leading-5 text-muted-foreground">试听、设计或导入后，选择一个声音即可打开对应配音节点。已有配音会保留原声音。</p>
      </section>}
    </div>}
    inspector={<div className="space-y-4">
      <section aria-label="当前绑定" className="space-y-2">
        <h3 className="text-sm font-semibold">试听与选择</h3>
        {selectedVersion ? <>
          <p className="text-xs text-emerald-500">已选用{selectedVersion.current ? '当前' : '历史'}声音 · {selectedVersion.slot}</p>
          {selectedVersion.url && <audio controls preload="none" className="w-full" src={selectedVersion.url} onPlay={pauseOtherWorkspaceAudio} />}
          <p className="text-xs leading-5 text-muted-foreground">使用其他声音会建立或打开对应配音节点；已有音频结果不会被替换。</p>
        </> : boundVersionId
          ? <div role="alert" className="space-y-2 text-xs text-amber-600">
            <p>上次选用的声音当前不可用。请刷新后重新选择。</p>
            <Button type="button" size="sm" variant="outline" disabled={versionsLoading} onClick={onRefreshVersions}>刷新声音版本</Button>
          </div>
          : <p className="text-xs leading-5 text-muted-foreground">选择一个声音即可固定到本次配音，并打开对应音频节点。</p>}
      </section>

      <section aria-label="可用角色声音" className="space-y-2 border-t pt-4">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">可用角色声音</h3>
          <Button type="button" size="sm" variant="ghost" disabled={versionsLoading || !project || !characterName} onClick={onRefreshVersions}>刷新</Button>
        </div>
        {versionsLoading && <p role="status" className="text-xs text-muted-foreground">正在读取声音版本…</p>}
        {versionsError && <div role="alert" className="space-y-2 text-xs text-destructive">
          <p>读取声音版本失败：{versionsError}</p>
          <Button type="button" size="sm" variant="outline" onClick={onRefreshVersions}>重试读取</Button>
        </div>}
        {!versionsLoading && !versionsError && versions.map(version => <article key={version.version_id} className="space-y-2 rounded-md border p-3">
          <p className="text-xs">{version.slot} · {version.current ? '当前声音' : '历史声音'}</p>
          {version.url && <audio controls preload="none" className="w-full" src={version.url} onPlay={pauseOtherWorkspaceAudio} />}
          <Button
            type="button"
            size="sm"
            variant={selectedVersion?.version_id === version.version_id ? 'default' : 'outline'}
            disabled={versionsLoading}
            onClick={() => {
              if (onUseVersion(version) === false) {
                setConnectionError(true);
                return;
              }
              setConnectionError(false);
              onBack();
            }}
          >使用此声音</Button>
        </article>)}
        {!versionsLoading && !versionsError && versions.length === 0 && <p className="text-xs leading-5 text-muted-foreground">还没有可用声音。可在中间的角色声音面板中设计、克隆或导入。</p>}
      </section>
    </div>}
    actions={<div className="min-w-0 text-sm">
      <p className="font-medium">{selectedVersion ? `已选用：${characterName} · ${selectedVersion.slot}` : boundVersionId ? '上次选用的声音不可用' : '尚未选用声音'}</p>
      <p className="mt-1 text-xs text-muted-foreground">选用声音会打开配音节点，不会自动开始生成。</p>
      {connectionError && <p role="alert" className="mt-2 text-xs text-destructive">没有打开配音节点。请刷新声音列表后重试。</p>}
    </div>}
  >
    <div onPlayCapture={pauseOtherWorkspaceAudio}>
      {selectedCharacter ? children : <section className="rounded-lg border p-6">
        <h3 className="font-medium">先选择一个项目角色</h3>
        <p className="mt-2 text-sm text-muted-foreground">角色声音设定、试听候选和已用声音都会按角色保存。</p>
      </section>}
    </div>
  </ProductionWorkspaceLayout>;
}
