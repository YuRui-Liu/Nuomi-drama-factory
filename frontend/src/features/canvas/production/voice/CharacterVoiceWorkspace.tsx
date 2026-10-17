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
  onBindVersion: (version: VoiceVersionRecord) => void;
  onRefreshVersions: () => void;
  onConnect: () => boolean | void;
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
  onBindVersion,
  onRefreshVersions,
  onConnect,
  children,
}: CharacterVoiceWorkspaceProps) {
  const [connectionError, setConnectionError] = useState(false);
  const selectedCharacter = characters.find(character => character.name === characterName);
  const bindingStatus = selectedVersion
    ? `${characterName} · ${selectedVersion.slot} · 固定已采用版本 ${selectedVersion.version_id}`
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
        <p className="mt-2 text-xs leading-5 text-muted-foreground">试听候选、采用声音和固定版本是分开的操作。新版本不会替换已有配音引用。</p>
      </section>}
    </div>}
    inspector={<div className="space-y-4">
      <section aria-label="当前绑定" className="space-y-2">
        <h3 className="text-sm font-semibold">试听与选择</h3>
        {selectedVersion ? <>
          <p className="text-xs text-emerald-500">已采用版本 {selectedVersion.version_id} · {selectedVersion.slot}{selectedVersion.current ? ' · 当前采用' : ' · 历史采用'}</p>
          {selectedVersion.url && <audio controls preload="none" className="w-full" src={selectedVersion.url} onPlay={pauseOtherWorkspaceAudio} />}
          <p className="text-xs leading-5 text-muted-foreground">此节点固定使用这个已采用版本。角色之后新增声音不会自动替换它。</p>
        </> : boundVersionId
          ? <div role="alert" className="space-y-2 text-xs text-amber-600">
            <p>绑定的声音版本 {boundVersionId} 当前不可用。请刷新后重新绑定。</p>
            <Button type="button" size="sm" variant="outline" disabled={versionsLoading} onClick={onRefreshVersions}>刷新声音版本</Button>
          </div>
          : <p className="text-xs leading-5 text-muted-foreground">尚未绑定已采用版本。先在下方选择一个声音版本。</p>}
      </section>

      <section aria-label="已采用声音版本" className="space-y-2 border-t pt-4">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">已采用版本</h3>
          <Button type="button" size="sm" variant="ghost" disabled={versionsLoading || !project || !characterName} onClick={onRefreshVersions}>刷新</Button>
        </div>
        {versionsLoading && <p role="status" className="text-xs text-muted-foreground">正在读取声音版本…</p>}
        {versionsError && <div role="alert" className="space-y-2 text-xs text-destructive">
          <p>读取声音版本失败：{versionsError}</p>
          <Button type="button" size="sm" variant="outline" onClick={onRefreshVersions}>重试读取</Button>
        </div>}
        {!versionsLoading && !versionsError && versions.map(version => <article key={version.version_id} className="space-y-2 rounded-md border p-3">
          <p className="text-xs">{version.identity_id || '默认身份'} · {version.slot} · 版本 {version.version_id}{version.current ? ' · 当前采用' : ' · 历史采用'}</p>
          {version.url && <audio controls preload="none" className="w-full" src={version.url} onPlay={pauseOtherWorkspaceAudio} />}
          <Button
            type="button"
            size="sm"
            variant={selectedVersion?.version_id === version.version_id ? 'default' : 'outline'}
            disabled={selectedVersion?.version_id === version.version_id}
            onClick={() => onBindVersion(version)}
          >{selectedVersion?.version_id === version.version_id ? '已绑定此版本' : '绑定此声音版本'}</Button>
        </article>)}
        {!versionsLoading && !versionsError && versions.length === 0 && <p className="text-xs leading-5 text-muted-foreground">还没有已采用声音。请先在中间的声音面板中试听候选并明确采用。</p>}
      </section>
    </div>}
    actions={<>
      <div className="min-w-0 text-sm">
        <p className="font-medium">{selectedVersion ? `已固定：${characterName} · 版本 ${selectedVersion.version_id}` : boundVersionId ? `绑定版本 ${boundVersionId} 不可用` : '尚未固定声音版本'}</p>
        <p className="mt-1 text-xs text-muted-foreground">连接只会创建配音节点和连线；配音生产需要在画布中的音频节点里单独发起。</p>
        {connectionError && <p role="alert" className="mt-2 text-xs text-destructive">没有创建配音节点。请确认当前选择的版本仍已采用，然后刷新声音版本。</p>}
      </div>
      <Button type="button" disabled={!selectedVersion || versionsLoading || Boolean(versionsError)} onClick={() => {
        if (onConnect() === false) {
          setConnectionError(true);
          return;
        }
        setConnectionError(false);
        onBack();
      }}>
        返回画布，连接配音节点
      </Button>
    </>}
  >
    <div onPlayCapture={pauseOtherWorkspaceAudio}>
      {selectedCharacter ? children : <section className="rounded-lg border p-6">
        <h3 className="font-medium">先选择一个项目角色</h3>
        <p className="mt-2 text-sm text-muted-foreground">角色声音设定、试听候选和已采用版本都会按角色保存。</p>
      </section>}
    </div>
  </ProductionWorkspaceLayout>;
}
