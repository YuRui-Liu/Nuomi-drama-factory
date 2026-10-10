import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

type Tags = { timbre: string[]; identity: string[]; personality: string[] };
type Asset = { id: string; name: string; description: string; tags: Tags; duration: number };
const groups = { timbre: "音色特点", identity: "身份特点", personality: "性格特点" };
const emptyTags = (): Tags => ({ timbre: [], identity: [], personality: [] });
const groupKeys = Object.keys(groups) as (keyof Tags)[];
const errorText = async (error: unknown) => {
  const response = (error as { response?: Response })?.response;
  if (response) { try { const data = await response.clone().json(); return typeof data.detail === "string" ? data.detail : "请求失败，请检查输入并重试"; } catch { /* use fallback */ } }
  return error instanceof Error ? error.message : "操作失败，请重试";
};

function TagFields({ tags, onChange }: { tags: Tags; onChange: (tags: Tags) => void }) {
  return <>{groupKeys.map(group => <label key={group} className="block space-y-1 text-sm">{groups[group]}
    <Input aria-label={groups[group]} value={tags[group].join("，")} placeholder="输入标签，用逗号分隔" onChange={e => onChange({ ...tags, [group]: e.target.value.split(/[,，]/) })} />
  </label>)}</>;
}
const cleanTags = (tags: Tags): Tags => Object.fromEntries(groupKeys.map(g => [g, [...new Set(tags[g].map(t => t.trim()).filter(Boolean))]])) as Tags;

export function VoiceAssetFavorite({ project, character, candidateId, description = "", suggestedTags }: { project: string; character: string; candidateId: string; description?: string; suggestedTags?: Partial<Tags> }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(character);
  const [tags, setTags] = useState<Tags>(() => ({ ...emptyTags(), ...suggestedTags }));
  const [notes, setNotes] = useState(description);
  const [assetId, setAssetId] = useState<string>();
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const qc = useQueryClient();
  async function save() {
    setBusy(true); setMessage("");
    try {
      const json = { name: name.trim(), description: notes, tags: cleanTags(tags) };
      if (assetId) {
        await api.patch(p`api/v1/voice-assets/${assetId}`, { json });
        setOpen(false);
      } else {
        const result = await api.post(p`api/v1/projects/${project}/characters/${character}/voice-candidates/${candidateId}/favorite`, { json }).json<{ data: { asset: Asset; created: boolean } }>();
        if (result.data.created) setOpen(false);
        else { const a = result.data.asset; setAssetId(a.id); setName(a.name); setTags(a.tags); setNotes(a.description); setMessage("此音频已收藏，已载入原名称和标签。修改后保存即可。"); }
      }
      await qc.invalidateQueries({ queryKey: ["global-voice-assets"] });
    } catch (e) { setMessage(await errorText(e)); } finally { setBusy(false); }
  }
  return <><Button size="sm" variant="outline" onClick={() => setOpen(true)}>收藏为全局配音资产</Button>
    <Dialog open={open} onOpenChange={value => { if (!busy) setOpen(value); }}><DialogContent><DialogHeader><DialogTitle>收藏配音音频</DialogTitle></DialogHeader>
      <label>资产名称<Input value={name} maxLength={100} onChange={e => setName(e.target.value)} /></label>
      <label>音色描述<Input value={notes} maxLength={2000} onChange={e => setNotes(e.target.value)} /></label>
      <TagFields tags={tags} onChange={setTags} /><p className="text-xs text-muted-foreground">身份和性格标签用于选角检索，不限制使用对象。</p>
      {message && <p role="status">{message}</p>}<Button disabled={busy || !name.trim()} onClick={save}>{busy ? "保存中…" : "保存"}</Button>
    </DialogContent></Dialog></>;
}

export function GlobalVoicePicker({ project, character, slot, reference }: { project: string; character: string; slot: string; reference: { path: string; sha256: string } }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [tags, setTags] = useState<Tags>(emptyTags);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [audio, setAudio] = useState<{ id: string; url: string }>();
  const [editing, setEditing] = useState<Asset>();
  const [deleteId, setDeleteId] = useState<string>();
  const [request, setRequest] = useState<{ asset: string; id: string; reference: typeof reference }>();
  const qc = useQueryClient();
  useEffect(() => () => { if (audio) URL.revokeObjectURL(audio.url); }, [audio]);
  const list = useQuery({ queryKey: ["global-voice-assets", q, tags, offset], enabled: open, queryFn: async () => {
    const params = new URLSearchParams({ q, offset: String(offset), limit: "20" });
    for (const g of groupKeys) for (const tag of cleanTags(tags)[g]) params.append(g, tag);
    return api.get(`api/v1/voice-assets?${params}`).json<{ data: { items: Asset[]; total: number } }>();
  } });
  const available = useQuery({ queryKey: ["global-voice-assets", "tags"], enabled: open, queryFn: () => api.get("api/v1/voice-assets/tags").json<{ data: Tags }>() });
  async function action(fn: () => Promise<void>) { setBusy(true); setMessage(""); try { await fn(); } catch (e) { setMessage(await errorText(e)); if ((e as { response?: Response }).response?.status === 409) { setRequest(undefined); await qc.invalidateQueries(); } } finally { setBusy(false); } }
  return <><Button size="sm" variant="outline" onClick={() => setOpen(true)}>从全局配音库选择</Button>
    <Dialog open={open} onOpenChange={value => { if (!busy) { setOpen(value); if (!value) setAudio(undefined); } }}><DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>全局配音库 · {character} / {slot}</DialogTitle></DialogHeader>
      <Input aria-label="搜索配音名称" placeholder="搜索配音名称" value={q} onChange={e => { setQ(e.target.value); setOffset(0); }} />
      {groupKeys.map(g => <fieldset key={g} className="space-y-2"><legend className="text-sm">{groups[g]}</legend><div className="flex flex-wrap gap-2">{available.data?.data[g].map(tag => <Button key={tag} size="sm" variant={tags[g].includes(tag) ? "default" : "outline"} onClick={() => { setTags({ ...tags, [g]: tags[g].includes(tag) ? tags[g].filter(t => t !== tag) : [...tags[g], tag] }); setOffset(0); }}>{tag}</Button>)}</div></fieldset>)}
      <p className="text-xs text-muted-foreground">同类标签匹配任意一项，不同分类同时满足。试听后采用为目标角色的声音参考。</p>
      {message && <p role="alert">{message}</p>}{list.isError && <p role="alert">加载失败<Button onClick={() => list.refetch()}>重试</Button></p>}{list.isLoading && <p>加载中…</p>}
      {list.data?.data.items.length === 0 && <p>暂无匹配配音，可先从已通过的角色配音手动收藏。</p>}
      {list.data?.data.items.map(asset => <article key={asset.id} className="space-y-2 rounded-lg border p-3"><h3>{asset.name} · {asset.duration.toFixed(1)} 秒</h3><p className="text-sm">{asset.description}</p>
        {groupKeys.map(g => <p key={g} className="text-xs text-muted-foreground">{groups[g]}：{asset.tags[g].join(" · ") || "未标注"}</p>)}
        {audio?.id === asset.id && <audio key={audio.url} controls autoPlay src={audio.url} />}
        <div className="flex gap-2"><Button size="sm" disabled={busy} onClick={() => action(async () => { setAudio(undefined); const blob = await api.get(p`api/v1/voice-assets/${asset.id}/audio`).blob(); setAudio({ id: asset.id, url: URL.createObjectURL(blob) }); })}>试听</Button>
          <Button size="sm" disabled={busy} onClick={() => action(async () => {
            const current = request?.asset === asset.id ? request : { asset: asset.id, id: crypto.randomUUID(), reference };
            setRequest(current);
            await api.post(p`api/v1/projects/${project}/characters/${character}/voice-assets/${asset.id}/adopt`, { json: { slot, request_id: current.id, expected_reference: current.reference } });
            await qc.invalidateQueries(); setOpen(false); setAudio(undefined); setRequest(undefined);
          })}>已试听，采用</Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(structuredClone(asset))}>编辑标签</Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => setDeleteId(asset.id)}>删除收藏</Button></div>
        {deleteId === asset.id && <div><p>删除全局收藏？已采用到项目的音频不受影响。</p><Button disabled={busy} onClick={() => action(async () => { await api.delete(p`api/v1/voice-assets/${asset.id}`); setDeleteId(undefined); setAudio(undefined); await qc.invalidateQueries({ queryKey: ["global-voice-assets"] }); })}>确认删除</Button><Button variant="ghost" onClick={() => setDeleteId(undefined)}>取消</Button></div>}
      </article>)}
      {editing && <section className="space-y-2 rounded-lg border p-3"><Input aria-label="资产名称" value={editing.name} onChange={e => setEditing({ ...editing, name: e.target.value })} /><Input aria-label="音色描述" value={editing.description} onChange={e => setEditing({ ...editing, description: e.target.value })} /><TagFields tags={editing.tags} onChange={value => setEditing({ ...editing, tags: value })} /><Button disabled={busy || !editing.name.trim()} onClick={() => action(async () => { await api.patch(p`api/v1/voice-assets/${editing.id}`, { json: { name: editing.name, description: editing.description, tags: cleanTags(editing.tags) } }); setEditing(undefined); await qc.invalidateQueries({ queryKey: ["global-voice-assets"] }); })}>保存修改</Button><Button variant="ghost" onClick={() => setEditing(undefined)}>取消</Button></section>}
      <div className="flex gap-2"><Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页</Button><Button disabled={offset + 20 >= (list.data?.data.total ?? 0)} onClick={() => setOffset(offset + 20)}>下一页</Button></div>
    </DialogContent></Dialog></>;
}
