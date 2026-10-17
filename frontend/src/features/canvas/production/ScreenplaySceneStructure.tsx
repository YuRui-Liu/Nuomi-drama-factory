import { useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import type { ScriptDocument, ScriptSceneIdentity, ScriptSceneIdentityUpdate } from '@/features/script-creation/types';
import {
  mergeScreenplayScenes,
  reorderScreenplayScene,
  screenplaySceneRanges,
  screenplaySelectionOffset,
  splitScreenplayScene,
  type ScreenplayStructureChange,
} from './screenplaySceneStructureUtils';

export interface SavedSceneStructureChange extends ScreenplayStructureChange {
  revisionId: string;
}

export type PendingStructure = {
  markdown: string;
  identities: ScriptSceneIdentityUpdate[];
  change: ScreenplayStructureChange;
  afterHeadings: string[];
};

type Props = {
  document: ScriptDocument;
  revisionId: string;
  markdown: string;
  saved: boolean;
  busy: boolean;
  selection: { text: string; start: number; end: number } | null;
  history: SavedSceneStructureChange[];
  onApply: (pending: PendingStructure) => Promise<void>;
};

function lineage(identity: ScriptSceneIdentity | undefined): string[] {
  return identity?.parent_ids ?? [];
}

export function ScreenplaySceneStructure({
  document,
  revisionId,
  markdown,
  saved,
  busy,
  selection,
  history,
  onApply,
}: Props) {
  const identities = document.revision.scene_identities ?? [];
  const ranges = useMemo(
    () => screenplaySceneRanges(markdown, document.revision.blocks, identities.map(item => item.id)),
    [document.revision.blocks, identities, markdown],
  );
  const [pending, setPending] = useState<PendingStructure | null>(null);
  const [splitHeading, setSplitHeading] = useState('');
  const [error, setError] = useState('');
  const identityById = new Map(identities.map(identity => [identity.id, identity]));
  const parsedIdentityMismatch = ranges.length !== identities.length;

  useEffect(() => {
    setPending(null);
    setError('');
  }, [document.id, revisionId]);

  function preview(
    operation: ScreenplayStructureChange['operation'],
    nextMarkdown: string | null,
    nextIds: string[],
    parentsById: Record<string, string[]>,
    summary: string,
    beforeSceneIds: string[],
    mergedSceneIds: string[] = [],
    newSceneHeading?: string,
  ) {
    if (!nextMarkdown) {
      setError('无法应用此操作；请确认选择位置或场次格式后重试。');
      return;
    }
    const after = screenplaySceneRanges(nextMarkdown);
    if (after.length !== nextIds.length) {
      setError('新稿场次解析数量与身份继承记录不一致，请检查场头格式。');
      return;
    }
    const identitiesUpdate = after.map((scene, index) => ({
      id: nextIds[index], ordinal: index + 1, heading: scene.heading,
      parent_ids: parentsById[nextIds[index]] ?? lineage(identityById.get(nextIds[index])),
    }));
    const retainedSceneIds = nextIds.filter(id => !mergedSceneIds.includes(id) && identityById.has(id));
    setPending({
      markdown: nextMarkdown,
      identities: identitiesUpdate,
      afterHeadings: after.map(scene => scene.heading),
      change: { operation, sourceRevisionId: revisionId, beforeSceneIds,
        retainedSceneIds, mergedSceneIds, newSceneHeading, summary },
    });
    setError('');
  }

  function move(index: number, delta: -1 | 1) {
    const target = index + delta;
    const ordered = ranges.map(scene => scene.id);
    [ordered[index], ordered[target]] = [ordered[target], ordered[index]];
    const beforeIds = [ranges[index].id, ranges[target].id];
    preview('reorder', reorderScreenplayScene(markdown, ranges, index, delta), ordered, {},
      `${ranges[index].heading} 与 ${ranges[target].heading} 调换顺序；两场身份保持。`, beforeIds);
  }

  function merge(index: number) {
    const first = ranges[index];
    const second = ranges[index + 1];
    if (!first || !second) return;
    const mergedId = `scene-${crypto.randomUUID()}`;
    const nextIds = ranges.flatMap((scene, sceneIndex) => sceneIndex === index
      ? [mergedId] : sceneIndex === index + 1 ? [] : [scene.id]);
    const parentsById: Record<string, string[]> = Object.fromEntries(
      identities.map(identity => [identity.id, identity.parent_ids]),
    );
    parentsById[mergedId] = [first.id, second.id];
    preview('merge', mergeScreenplayScenes(markdown, ranges, index), nextIds, parentsById,
      `合并相邻两场；新场身份 ${mergedId} 记录 ${first.id}、${second.id} 为来源。`,
      [first.id, second.id], [first.id, second.id]);
  }

  function split() {
    if (!selection?.text) return;
    const caret = screenplaySelectionOffset(selection.start, markdown);
    const sceneIndex = ranges.findIndex(scene => caret >= scene.start && caret < scene.end);
    const source = ranges[sceneIndex];
    if (!source) return;
    const nextHeading = splitHeading.trim() || `第${source.ordinal + 1}场 新场景`;
    const nextMarkdown = splitScreenplayScene(markdown, ranges, selection.start, nextHeading);
    const nextIds = ranges.map(scene => scene.id);
    const createdId = `scene-${crypto.randomUUID()}`;
    nextIds.splice(sceneIndex + 1, 0, createdId);
    const parentsById: Record<string, string[]> = Object.fromEntries(
      identities.map(identity => [identity.id, identity.parent_ids]),
    );
    parentsById[createdId] = [source.id];
    preview('split', nextMarkdown, nextIds, parentsById,
      `从 ${source.id} 拆出新场；原场身份保留给前半段，新场继承自原场。`,
      [source.id], [], nextHeading);
  }

  return <section className="space-y-3 rounded-lg border p-4" aria-label="场次结构编辑">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div><h3 className="text-sm font-medium">场次顺序与结构</h3>
        <p className="text-xs text-muted-foreground">场序按正文保存。重排保留身份；拆分和合并记录继承关系，并提示导演语义与下游场景引用复核。</p></div>
      <span className="text-xs text-muted-foreground">{ranges.length} 场 · 版本 {revisionId.slice(0, 8)}</span>
    </div>
    {!saved && <p className="text-sm text-amber-700">先保存当前正文，再调整场次结构。</p>}
    {parsedIdentityMismatch && <p role="alert" className="text-sm text-destructive">剧本文本场头与持久场景身份数量不匹配，结构操作已停用；请刷新版本后重试。</p>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {ranges.length > 0 && <ol className="space-y-2">
      {ranges.map((scene, index) => <li key={scene.id} className="rounded border p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0"><p className="text-sm font-medium">{scene.ordinal}. {scene.heading}</p>
            <p className="text-xs text-muted-foreground" title={scene.id}>场景身份 {scene.id.slice(0, 18)}</p>
            {lineage(identityById.get(scene.id)).length > 0 && <p className="text-xs text-muted-foreground">继承来源：{lineage(identityById.get(scene.id)).join('、')}</p>}</div>
          <div className="flex flex-wrap gap-1">
            <Button size="sm" variant="outline" disabled={busy || !saved || parsedIdentityMismatch || index === 0}
              onClick={() => move(index, -1)}>上移</Button>
            <Button size="sm" variant="outline" disabled={busy || !saved || parsedIdentityMismatch || index === ranges.length - 1}
              onClick={() => move(index, 1)}>下移</Button>
            <Button size="sm" variant="outline" disabled={busy || !saved || parsedIdentityMismatch || index === ranges.length - 1}
              onClick={() => merge(index)}>与下一场合并</Button>
          </div>
        </div>
      </li>)}
    </ol>}
    <div className="grid gap-2 rounded border p-3 md:grid-cols-[1fr_auto]">
      <label className="grid gap-1 text-xs">拆分后的新场场头
        <input className="rounded border px-2 py-1 text-sm" value={splitHeading}
          onChange={event => setSplitHeading(event.target.value)} placeholder="例如：第2场 新场景" />
      </label>
      <Button variant="outline" disabled={busy || !saved || parsedIdentityMismatch || !selection?.text}
        onClick={split}>按当前选区拆场</Button>
      <p className="text-xs text-muted-foreground md:col-span-2">选区需从某场正文中的完整行首开始；当前选区不满足时会提示原因。</p>
    </div>
    {pending && <section className="space-y-3 rounded-md border border-primary/40 bg-muted/30 p-3" aria-label="场次结构变更预览">
      <div><h4 className="text-sm font-medium">保存前影响预览</h4><p className="text-sm">{pending.change.summary}</p></div>
      <div className="grid gap-3 md:grid-cols-2"><div><p className="mb-1 text-xs font-medium">当前场序</p>
          <ul className="space-y-1 text-xs">{ranges.map(scene => <li key={scene.id}>{scene.ordinal}. {scene.heading} <code>{scene.id.slice(0, 12)}</code></li>)}</ul></div>
        <div><p className="mb-1 text-xs font-medium">保存后场序</p>
          <ul className="space-y-1 text-xs">{pending.afterHeadings.map((heading, index) => <li key={`${index}:${heading}`}>{index + 1}. {heading} <code>{pending.identities[index].id.slice(0, 12)}</code></li>)}</ul></div>
      </div>
      <p className="text-xs text-amber-700">场景语义、导演方案及引用场次会按新版本标记为需复核；已生成的对白音频不会被删除，也不会自动重生成。</p>
      {pending.change.sourceRevisionId !== revisionId && <p role="alert" className="text-sm text-amber-700">正文版本已变化，这份结构预览已失效，请基于当前版本重新操作。</p>}
      <div className="flex justify-end gap-2"><Button variant="ghost" disabled={busy} onClick={() => setPending(null)}>取消</Button>
        <Button disabled={busy || !saved || parsedIdentityMismatch || pending.change.sourceRevisionId !== revisionId}
          onClick={() => void onApply(pending)}>确认保存结构变更</Button></div>
    </section>}
    {history.length > 0 && <details className="rounded border p-3"><summary className="cursor-pointer text-sm">场景身份继承记录（{history.length}）</summary>
      <ul className="mt-2 space-y-2 text-xs">{history.slice(-8).reverse().map((change, index) => <li key={`${change.revisionId}:${index}`}>
        <strong>{change.operation === 'reorder' ? '重排' : change.operation === 'split' ? '拆分' : '合并'}</strong>
        <span> · {change.summary} · 版本 {change.revisionId.slice(0, 8)}</span>
        {change.mergedSceneIds.length > 0 && <span> · 合并来源 {change.mergedSceneIds.join(', ')}</span>}
      </li>)}</ul>
    </details>}
  </section>;
}
