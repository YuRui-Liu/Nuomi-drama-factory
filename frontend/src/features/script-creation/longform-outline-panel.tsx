import { useEffect, useRef, useState } from 'react';
import { longformStoryApi, type LongformDocument, type OutlineItem, type SaveOutline } from '@/api/longform-story';
import { ContinuityReviewPanel } from './continuity-review-panel';

const kinds = { series_arc: '全剧主线', story_arc: '故事弧', episode: '分集' };
const field = 'rounded-lg border border-white/10 bg-background px-2 py-1.5 text-sm';
const message = (error: unknown) => error instanceof Error ? error.message : '操作失败，请重试';
function normalize(items: OutlineItem[]) {
  const counts = new Map<string | null, number>();
  return items.map(item => { const ordinal = (counts.get(item.parent_id) ?? 0) + 1; counts.set(item.parent_id, ordinal); return { ...item, ordinal }; });
}
function descendants(items: OutlineItem[], id: string): Set<string> {
  const found = new Set([id]);
  const children = new Map<string, string[]>();
  for (const item of items) if (item.parent_id) children.set(item.parent_id, [...(children.get(item.parent_id) ?? []), item.id]);
  const queue = [id];
  while (queue.length) for (const child of children.get(queue.pop()!) ?? []) if (!found.has(child)) { found.add(child); queue.push(child); }
  return found;
}
function ordered(items: OutlineItem[], episode: number) {
  const included = new Set(items.filter(item => !episode || (item.episode_start === null || item.episode_start <= episode) && (item.episode_end === null || item.episode_end >= episode)).map(item => item.id));
  for (const id of [...included]) {
    let parent = items.find(item => item.id === id)?.parent_id;
    const seen = new Set<string>();
    while (parent && !seen.has(parent)) { seen.add(parent); included.add(parent); parent = items.find(item => item.id === parent)?.parent_id; }
  }
  const rows: { item: OutlineItem; depth: number }[] = [];
  const visited = new Set<string>();
  const visit = (parent: string | null, depth: number) => {
    for (const item of items.filter(item => item.parent_id === parent).sort((a, b) => a.ordinal - b.ordinal)) {
      if (visited.has(item.id)) continue;
      visited.add(item.id);
      if (included.has(item.id)) rows.push({ item, depth });
      visit(item.id, depth + 1);
    }
  };
  visit(null, 0);
  return rows;
}

/** Mount separately from the Markdown DraftManager so structured revisions have one editor. */
export function LongformOutlinePanel(props: { project: string; documentId: string; onSaved?: (document: LongformDocument) => void }) {
  return <OutlineSession key={`${props.project}:${props.documentId}`} {...props} />;
}

function OutlineSession({ project, documentId, onSaved }: { project: string; documentId: string; onSaved?: (document: LongformDocument) => void }) {
  const [document, setDocument] = useState<LongformDocument>();
  const [items, setItems] = useState<OutlineItem[]>([]);
  const [episode, setEpisode] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const alive = useRef(false);
  const flight = useRef(false);
  const pending = useRef<{ key: string; body: SaveOutline } | undefined>(undefined);
  useEffect(() => {
    let current = true; alive.current = true;
    setError(''); setBusy(true);
    void longformStoryApi.getOutline(project, documentId).then(value => {
      if (!current) return;
      if (value.kind !== 'outline') throw new Error('当前文档不是层级大纲');
      setDocument(value); setItems(value.revision.outline_items ?? []); pending.current = undefined;
    }).catch(reason => { if (current) setError(message(reason)); }).finally(() => { if (current) setBusy(false); });
    return () => { current = false; alive.current = false; };
  }, [project, documentId, reload]);
  const dirty = !!document && JSON.stringify(items) !== JSON.stringify(document.revision.outline_items ?? []);
  function update(id: string, patch: Partial<OutlineItem>) { setItems(current => normalize(current.map(item => item.id === id ? { ...item, ...patch } : item))); }
  function add(kind: OutlineItem['kind']) {
    setEpisode(0);
    setItems(current => normalize([...current, { id: crypto.randomUUID(), parent_id: null, ordinal: 1, kind, title: `新${kinds[kind]}`, episode_start: kind === 'episode' ? 1 : null, episode_end: kind === 'episode' ? 1 : null }]));
  }
  async function save() {
    if (!document || flight.current || !dirty) return;
    flight.current = true; setBusy(true); setError('');
    const key = JSON.stringify([document.current_revision_id, items]);
    if (pending.current?.key !== key) pending.current = { key, body: { base_revision_id: document.current_revision_id, items: structuredClone(items), client_mutation_id: crypto.randomUUID() } };
    try {
      const saved = await longformStoryApi.saveOutline(project, documentId, pending.current.body);
      if (!alive.current) return;
      setDocument(saved); setItems(saved.revision.outline_items ?? []); pending.current = undefined; onSaved?.(saved);
    } catch (reason) { if (alive.current) setError(`${message(reason)}。本地修改已保留，重试将使用同一请求。`); }
    finally { flight.current = false; if (alive.current) setBusy(false); }
  }
  if (!document) return <section className="p-4">{error ? <><p role="alert">{error}</p><button onClick={() => setReload(value => value + 1)}>重新读取大纲</button></> : <p role="status">正在读取大纲…</p>}</section>;
  return <section aria-label="长篇故事大纲" className="flex min-h-0 flex-col gap-4 p-4 text-foreground">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div><h2 className="font-semibold">{document.title}</h2><p className="mt-1 text-xs text-muted-foreground">当前版本：{document.current_revision_id}</p><p className="text-xs text-muted-foreground">{document.adopted_revision_id === document.current_revision_id ? '当前版本已采用' : '当前版本未采用'}{document.adopted_revision_id && document.adopted_revision_id !== document.current_revision_id ? ` · 已采用版本：${document.adopted_revision_id}` : ''}</p></div>
      <button disabled={!dirty || busy || items.some(item => !item.title.trim())} onClick={() => void save()} className="rounded-lg bg-accent px-3 py-2 text-sm text-black disabled:opacity-40">{busy ? '正在保存…' : '保存大纲'}</button>
    </header>
    {error && <div className="space-y-2"><p role="alert" className="text-sm text-red-300">{error}</p><div className="flex flex-wrap gap-3 text-xs">
      <button type="button" onClick={() => {
        const url = URL.createObjectURL(new Blob([JSON.stringify({ document_id: documentId, base_revision_id: document.current_revision_id, items }, null, 2)], { type: 'application/json' }));
        const link = window.document.createElement('a'); link.href = url; link.download = 'outline-local-draft.json'; link.click(); URL.revokeObjectURL(url);
      }} className="text-accent">下载本地草稿</button>
      <button type="button" disabled={busy} onClick={() => { if (!flight.current) { setBusy(true); setReload(value => value + 1); } }} className="text-muted-foreground">放弃本地修改并读取最新版本</button>
    </div></div>}
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <label>查看集数 <select aria-label="查看集数" className={field} value={episode} onChange={event => setEpisode(Number(event.target.value))}><option value={0}>全部 1–100 集</option>{Array.from({ length: 100 }, (_, index) => <option key={index + 1} value={index + 1}>第 {index + 1} 集</option>)}</select></label>
      {(Object.keys(kinds) as OutlineItem['kind'][]).map(kind => <button key={kind} disabled={busy || items.length >= 500} onClick={() => add(kind)} className={field}>添加{kinds[kind]}</button>)}
      {dirty && <span className="text-xs text-amber-300">未保存修改</span>}
    </div>
    <p className="text-xs text-muted-foreground">大纲为创作规划；保存不会自动采用为连续性事实。来源版本保持固定。</p>
    <div className="max-h-[60vh] space-y-2 overflow-auto" aria-label="大纲层级">
      {ordered(items, episode).map(({ item, depth }) => { const blocked = descendants(items, item.id); return <article key={item.id} className="space-y-2 rounded-xl border border-white/10 p-3" style={{ marginLeft: Math.min(depth, 6) * 16 }}>
        <div className="flex items-center gap-2 text-xs text-muted-foreground"><span>{kinds[item.kind]}</span><span>#{item.ordinal}</span></div>
        <input aria-label={`${kinds[item.kind]}标题 ${item.id}`} value={item.title} disabled={busy} maxLength={2000} onChange={event => update(item.id, { title: event.target.value })} className={`${field} w-full`} />
        <div className="flex flex-wrap gap-2 text-xs">
          <label>上级 <select aria-label={`上级 ${item.id}`} className={field} disabled={busy} value={item.parent_id ?? ''} onChange={event => update(item.id, { parent_id: event.target.value || null })}><option value="">顶层</option>{items.filter(parent => parent.kind !== 'episode' && !blocked.has(parent.id)).map(parent => <option key={parent.id} value={parent.id}>{parent.title}</option>)}</select></label>
          <label>起始集 <input aria-label={`起始集 ${item.id}`} className={`${field} w-20`} type="number" min={1} max={100} disabled={busy} value={item.episode_start ?? ''} onChange={event => { const value = event.target.value ? Number(event.target.value) : null; update(item.id, { episode_start: value, ...(item.kind === 'episode' ? { episode_end: value } : {}) }); }} /></label>
          {item.kind !== 'episode' && <label>结束集 <input aria-label={`结束集 ${item.id}`} className={`${field} w-20`} type="number" min={1} max={100} disabled={busy} value={item.episode_end ?? ''} onChange={event => update(item.id, { episode_end: event.target.value ? Number(event.target.value) : null })} /></label>}
        </div>
        <p className="break-all text-xs text-muted-foreground">{item.source_revision_id ? `来源：${item.source_document_id ?? '未记录文档'} · ${item.source_revision_id} · ${item.source_locator ?? '未记录位置'}` : '来源版本未记录'}</p>
      </article>; })}
      {!items.length && <p className="text-sm text-muted-foreground">尚无层级条目。可添加全剧主线、故事弧和分集；不会从旧 Markdown 推断持久结构。</p>}
    </div>
    <ContinuityReviewPanel project={project} documentId={documentId} episode={episode || 1} />
  </section>;
}
