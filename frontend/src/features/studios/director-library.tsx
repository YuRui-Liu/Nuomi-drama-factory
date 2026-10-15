import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { jsonWithBackendError } from '@/lib/api-errors';
import { Button } from '@/components/ui/button';
import { useAuthStore } from '@/stores/auth-store';
import { directorPresets, type DirectorConfig } from './director-config';

const preferenceKeys = ['pace', 'camera_motion', 'composition', 'performance', 'method'] as const;
type Preferences = Pick<DirectorConfig, typeof preferenceKeys[number]>;
export type DirectorCard = { id: string; revision: number; name: string; description: string; creative_types: string[]; tags: string[]; preferences: Preferences; builtin?: boolean; source_id?: string | null; source_revision?: number | null };
const genres: Record<string, string> = { TVC: '广告', THRILLER: '悬疑', TALK: '口播', ANIMATION: '动画', DOCUMENTARY: '纪录片', MUSIC: 'MV', OBSERVE: '情感', REVEAL: '悬疑', ACTION: '动作', EMOTION: '情感' };
export const builtinDirectors: DirectorCard[] = directorPresets.map(preset => ({ id: `builtin:${preset.code}`, revision: 1, name: preset.name, description: preset.description, creative_types: [genres[preset.code]], tags: preset.tags, preferences: Object.fromEntries(preferenceKeys.map(key => [key, preset.data[key]])) as Preferences, builtin: true }));
export function filterDirectors(cards: DirectorCard[], source: string, search: string, genre: string, tags: string[]) {
  return cards.filter(card => (source === 'all' || (source === 'builtin' ? card.builtin : !card.builtin)) && card.name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()) && (!genre || card.creative_types.includes(genre)) && (!tags.length || tags.some(tag => card.tags.includes(tag))));
}
const fieldClass = 'w-full rounded-md border bg-background p-2 text-sm';
const splitTags = (value: string) => [...new Set(value.split(/[,，、]/).map(tag => tag.trim()).filter(Boolean))];

export function DirectorLibrary({ name, config, dirty, disabled = false, compact = false, onChoose }: { name: string; config: DirectorConfig; dirty: boolean; disabled?: boolean; compact?: boolean; onChoose: (card: DirectorCard) => void }) {
  const username = useAuthStore(state => state.username);
  const listing = useQuery({ queryKey: ['director-library', username], queryFn: () => api.get('api/v1/director-library').json<{ data: DirectorCard[] }>(), gcTime: 0 });
  const [source, setSource] = useState('all');
  const [search, setSearch] = useState('');
  const [genre, setGenre] = useState('');
  const [tags, setTags] = useState<string[]>([]);
  const [selected, setSelected] = useState<DirectorCard | null>(null);
  const [pending, setPending] = useState<DirectorCard | null>(null);
  const [form, setForm] = useState<{ name: string; description: string; creative_types: string; tags: string; update: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const signature = useRef('');
  signature.current = JSON.stringify({ name, config });
  const cards = [...builtinDirectors, ...(listing.data?.data ?? [])];
  const choose = (card: DirectorCard) => { setSelected(card); setPending(null); onChoose(card); };
  const openSave = (update = false) => { setError(''); setForm({ name, description: selected?.description ?? '', creative_types: selected?.creative_types.join('，') ?? '', tags: selected?.tags.join('，') ?? '', update }); };
  const save = async () => {
    if (!form) return;
    const submittedSignature = signature.current;
    setBusy(true); setError('');
    try {
      const body = { name: form.name.trim(), description: form.description, creative_types: splitTags(form.creative_types), tags: splitTags(form.tags), preferences: Object.fromEntries(preferenceKeys.map(key => [key, config[key]])), source_id: selected?.id ?? null, source_revision: selected?.revision ?? null };
      const result = await jsonWithBackendError<{ data: DirectorCard }>(form.update && selected ? api.put(`api/v1/director-library/${encodeURIComponent(selected.id)}`, { json: { ...body, expected_revision: selected.revision } }) : api.post('api/v1/director-library', { json: body }));
      setSelected(result.data); setForm(null); void listing.refetch();
      const newerEdits = signature.current !== submittedSignature;
      setNotice(newerEdits ? '已收藏提交时的副本；保存期间新增的编辑仍保留，尚未收藏，请处理后再切换。' : '已收藏到我的导演；项目草稿与启用版本未改变。');
      if (pending && !newerEdits) choose(pending);
    } catch (failure) { setError(`收藏失败：${failure instanceof Error ? failure.message : String(failure)}。版本冲突请刷新列表后重新选入，或另存为新导演；当前编辑内容已保留。`); }
    finally { setBusy(false); }
  };
  const remove = async () => {
    if (!selected || selected.builtin || !window.confirm('删除此个人导演？项目已保存配置和历史任务不受影响，当前编辑副本仍保留。')) return;
    setBusy(true); setError('');
    try { await jsonWithBackendError(api.delete(`api/v1/director-library/${encodeURIComponent(selected.id)}?expected_revision=${selected.revision}`)); setSelected(null); void listing.refetch(); setNotice('已删除收藏，当前编辑副本仍保留。'); }
    catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { setBusy(false); }
  };
  return <div className="space-y-3" aria-label="导演技能库">
    <div hidden={compact} className="space-y-3">
    <div className="flex flex-wrap gap-2">{[['all', '全部'], ['builtin', '内置导演'], ['mine', '我的导演']].map(([value, label]) => <Button key={value} variant={source === value ? 'default' : 'outline'} aria-pressed={source === value} onClick={() => setSource(value)}>{label}</Button>)}</div>
    <div className="grid gap-2 sm:grid-cols-2"><input aria-label="搜索导演名称" className={fieldClass} placeholder="搜索导演名称" value={search} onChange={event => setSearch(event.target.value)} /><select aria-label="创作类型筛选" className={fieldClass} value={genre} onChange={event => setGenre(event.target.value)}><option value="">全部创作类型</option>{[...new Set(cards.flatMap(card => card.creative_types))].map(type => <option key={type}>{type}</option>)}</select></div>
    <details><summary className="cursor-pointer text-xs text-muted-foreground">特点标签筛选{tags.length ? ` · 已选 ${tags.length}` : ''}</summary><div className="mt-2 flex flex-wrap gap-2" aria-label="特点标签筛选">{[...new Set(cards.flatMap(card => card.tags))].map(tag => <button type="button" key={tag} aria-pressed={tags.includes(tag)} className={`rounded border px-2 py-1 text-xs ${tags.includes(tag) ? 'border-primary bg-primary/10' : ''}`} onClick={() => setTags(current => current.includes(tag) ? current.filter(value => value !== tag) : [...current, tag])}>{tag}</button>)}</div></details>
    {listing.isError && <p role="alert">个人导演加载失败，内置导演仍可使用。<Button variant="outline" onClick={() => void listing.refetch()}>重试个人导演列表</Button></p>}
    <div className="grid grid-cols-1 gap-2">{filterDirectors(cards, source, search, genre, tags).map(card => <button type="button" key={card.id} aria-label={card.name} aria-pressed={selected?.id === card.id} disabled={disabled || busy} className={`rounded-lg border p-3 text-left ${selected?.id === card.id ? 'border-primary bg-primary/10' : 'hover:bg-muted/50'}`} onClick={() => dirty ? setPending(card) : choose(card)}><span className="font-medium">{card.name}</span><span className="ml-2 text-xs text-muted-foreground">{card.builtin ? '内置' : `个人 · v${card.revision}`}</span><p className="my-2 text-xs text-muted-foreground">{card.description}</p><p className="text-xs">{[...card.creative_types, ...card.tags].join(' · ')}</p></button>)}</div>
    {!filterDirectors(cards, source, search, genre, tags).length && <p className="text-sm text-muted-foreground">暂无符合条件的导演，可调整筛选或收藏当前方法。</p>}
    </div>
    <p className="text-xs text-muted-foreground">选入只修改编辑器；收藏跨项目可用，仅自己可见。选择和收藏不提交生成任务。</p>
    <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={busy || disabled} onClick={() => openSave()}>另存为我的导演</Button>{selected && !selected.builtin && <><Button variant="outline" disabled={busy || disabled} onClick={() => openSave(true)}>更新我的导演</Button><Button variant="outline" disabled={busy || disabled} onClick={() => void remove()}>删除我的导演</Button><Button variant="outline" onClick={() => void listing.refetch()}>刷新个人导演列表</Button></>}</div>
    {pending && !form && <div role="dialog" aria-label="未保存修改" className="space-y-3 rounded-lg border p-4"><p>当前导演方法尚未保存，如何切换？</p><Button onClick={() => openSave()}>另存后切换</Button><Button variant="outline" onClick={() => choose(pending)}>放弃修改并切换</Button><Button variant="outline" onClick={() => setPending(null)}>取消</Button></div>}
    {form && <form aria-label="收藏导演" className="space-y-3 rounded-lg border p-4" onSubmit={event => { event.preventDefault(); void save(); }}><label className="block text-sm">收藏名称<input required maxLength={100} disabled={busy} className={fieldClass} value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} /></label><label className="block text-sm">简介<textarea maxLength={1000} disabled={busy} className={fieldClass} value={form.description} onChange={event => setForm({ ...form, description: event.target.value })} /></label><label className="block text-sm">创作类型（逗号分隔）<input disabled={busy} className={fieldClass} value={form.creative_types} onChange={event => setForm({ ...form, creative_types: event.target.value })} /></label><label className="block text-sm">特点标签（逗号分隔）<input disabled={busy} className={fieldClass} value={form.tags} onChange={event => setForm({ ...form, tags: event.target.value })} /></label><p className="text-xs text-muted-foreground">类型和特点各最多 20 项，每项 1–30 字。仅收藏五项导演偏好，不携带改编授权或来源文档。</p><Button type="submit" disabled={busy || !form.name.trim()}>{busy ? '正在保存…' : form.update ? '确认更新收藏' : '确认另存收藏'}</Button><Button type="button" variant="outline" disabled={busy} onClick={() => { setForm(null); setPending(null); }}>取消收藏</Button>{form.update && <Button type="button" variant="outline" disabled={busy} onClick={() => setForm({ ...form, update: false })}>改为另存新导演</Button>}</form>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
  </div>;
}
