import { useState, useEffect } from "react";
import { toast } from "sonner";
import { useNavigate } from "@tanstack/react-router";
import { useIdentityOwnerIndex } from "@/lib/queries/characters";
import { useScenes } from "@/lib/queries/scenes";
import { useProps } from "@/lib/queries/props";
import { useSaveEpisodeAssetBindings } from "@/lib/queries/episodes";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import type { Episode } from "@/types/episode";

export function EpisodeAssetsPanel({ project, episode, onClose, actions, parsing = false }: {
  project: string; episode: Episode; onClose: () => void; actions?: React.ReactNode; parsing?: boolean;
}) {
  const identities = useIdentityOwnerIndex(project);
  const scenes = useScenes(project);
  const props = useProps(project);
  const save = useSaveEpisodeAssetBindings(project, episode.number);
  const navigate = useNavigate();
  const initial = { identity_ids: episode.identity_ids ?? [], scene_ids: (episode.scene_menu ?? []).map(s => s.scene_id), prop_ids: (episode.prop_menu ?? []).map(p => p.prop_id) };
  const [draft, setDraft] = useState(initial);
  const [baseline, setBaseline] = useState(JSON.stringify(initial));
  const incoming = JSON.stringify(initial);
  const [tab, setTab] = useState<keyof typeof initial>("identity_ids");
  const [discard, setDiscard] = useState(false);
  const [search, setSearch] = useState("");
  const dirty = JSON.stringify(draft) !== baseline;
  const conflict = incoming !== baseline && dirty;
  useEffect(() => {
    if (incoming !== baseline && !dirty) {
      setDraft(JSON.parse(incoming));
      setBaseline(incoming);
    }
  }, [incoming, baseline, dirty]);
  const catalogs = {
    identity_ids: identities.identities.map(i => ({ id: i.id, name: `${i.owner} · ${i.id}` })),
    scene_ids: (scenes.data?.data ?? []).map(s => ({ id: s.name, name: s.name })),
    prop_ids: (props.data?.data ?? []).map(p => ({ id: p.name, name: p.name })),
  };
  const options = [...catalogs[tab], ...draft[tab].filter(id => !catalogs[tab].some(item => item.id === id)).map(id => ({ id, name: `${id}（原关联，项目中未找到）` }))];
  const loading = identities.isLoading || scenes.isLoading || props.isLoading;
  const readError = identities.isError || scenes.isError || props.isError;
  const close = () => { if (save.isPending) return; if (dirty) setDiscard(true); else onClose(); };
  return <Sheet open onOpenChange={open => { if (!open) close(); }}>
    <SheetContent className="sm:max-w-2xl overflow-hidden gap-0">
      <SheetHeader className="shrink-0 border-b pr-12"><SheetTitle>第 {episode.number} 集 · 本集资产</SheetTitle><SheetDescription>自动解析需要先完成并激活镜头方案；也可手动绑定项目已有的人物身份、场景和道具。解除关联不会删除资产或已生成媒体。</SheetDescription></SheetHeader>
      <div className="min-h-0 flex-1 overflow-y-auto p-4 space-y-4">
        <fieldset disabled={dirty || save.isPending}>{actions}</fieldset>
        {conflict && <p role="alert" className="text-destructive">本集资产已被其他任务更新。请关闭后重新打开，检查最新结果再保存。</p>}
        <div className="flex gap-2">{([['identity_ids','人物'],['scene_ids','场景'],['prop_ids','道具']] as const).map(([key,label]) => <Button key={key} variant={tab === key ? 'default' : 'outline'} onClick={() => {setTab(key);setSearch('');}}>{label} {draft[key].length}</Button>)}</div>
        <input aria-label="搜索项目资产" placeholder="搜索项目资产" className="h-9 w-full rounded border bg-background px-3" value={search} onChange={e => setSearch(e.target.value)} />
        {loading && <p role="status">正在读取项目资产…</p>}
        {parsing && <p role="status">资产解析进行中，完成后可调整并保存绑定。</p>}
        {readError && <p role="alert" className="text-destructive">部分资产读取失败，请关闭后重试。</p>}
        <div className="space-y-2">{options.filter(item => item.name.includes(search)).map(item => <label key={item.id} className="flex items-center gap-3 rounded border p-3"><input type="checkbox" checked={draft[tab].includes(item.id)} onChange={e => setDraft(old => ({ ...old, [tab]: e.target.checked ? [...old[tab], item.id] : old[tab].filter(id => id !== item.id) }))} /><span className="min-w-0 break-all">{item.name}</span></label>)}</div>
        {!loading && !options.length && <p className="text-muted-foreground">项目中暂无此类资产。可先解析，或在资产中心新建后回来绑定。</p>}
        <Button variant="outline" disabled={dirty || save.isPending} onClick={() => navigate({to: '/projects/$project/characters', params: { project }, search: {type: tab === 'identity_ids' ? 'identity' : tab === 'scene_ids' ? 'scene' : 'prop'} as never})}>前往资产中心管理 / 新建</Button>
        {dirty && <p className="text-xs text-muted-foreground">请先保存绑定再前往资产中心。</p>}
      </div>
      <div className="shrink-0 border-t p-4 flex items-center justify-between gap-2">
        {discard ? <><span>放弃未保存的绑定？</span><Button variant="outline" onClick={() => setDiscard(false)}>继续编辑</Button><Button variant="destructive" onClick={onClose}>放弃</Button></> : <><span className="text-xs text-muted-foreground">{dirty ? '有未保存修改' : '绑定已保存'}</span><Button disabled={!dirty || parsing || conflict || loading || readError || save.isPending} onClick={async () => { try { const result = await save.mutateAsync(draft); if (!result.ok) { toast.error(typeof result.error === 'string' ? result.error : '保存失败'); return; } toast.success('绑定已保存；既有镜头引用请复核'); onClose(); } catch { toast.error('保存失败，修改已保留'); } }}> {save.isPending ? '保存中…' : '保存绑定'}</Button></>}
      </div>
    </SheetContent>
  </Sheet>;
}
