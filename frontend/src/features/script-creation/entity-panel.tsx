import { useCallback, useEffect, useRef, useState } from "react";
import { scriptCreationApi } from "./api";
import type { EntityAppearance, EntityInput, EntityRelation, NarrativeAsset, NarrativeAssetType, NarrativeEntity, ScriptDocument } from "./types";

const kinds: Record<string, NarrativeAssetType> = { people: "character", scenes: "scene", props: "prop" };
const field = "w-full rounded border border-white/15 bg-[#17191D] p-2 text-xs text-white";
const action = "rounded border border-white/15 px-2 py-1.5 text-xs disabled:opacity-35";
const message = (cause: unknown) => cause instanceof Error ? cause.message : "操作失败，请重试";

export function EntityPanel({ project, document, documents, allSaved }: { project: string; document: ScriptDocument | null; documents: ScriptDocument[]; allSaved: boolean }) {
  const [entities, setEntities] = useState<NarrativeEntity[]>([]);
  const [assets, setAssets] = useState<NarrativeAsset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<NarrativeEntity | "new" | null>(null);
  const [block, setBlock] = useState("");
  const [name, setName] = useState("");
  const [asset, setAsset] = useState("");
  const [create, setCreate] = useState(false);
  const [assetName, setAssetName] = useState("");
  const [description, setDescription] = useState("");
  const [relations, setRelations] = useState<EntityRelation[]>([]);
  const [appearances, setAppearances] = useState<EntityAppearance[]>([]);
  const [appearanceKind, setAppearanceKind] = useState<EntityAppearance["kind"]>("first_appearance");
  const [episode, setEpisode] = useState("planned:1");
  const pending = useRef<{ payload: string; mutation: string } | null>(null);
  const generation = useRef(0);
  const assetType = document ? kinds[document.kind] : undefined;
  const refresh = useCallback(async () => {
    const seq = ++generation.current;
    setLoading(true); setError("");
    try {
      const [items, choices] = await Promise.all([scriptCreationApi.listEntities(project), assetType ? scriptCreationApi.listEntityAssets(project, assetType) : Promise.resolve([])]);
      if (seq === generation.current) { setEntities(items); setAssets(choices); }
    } catch (cause) { if (seq === generation.current) setError(message(cause)); }
    finally { if (seq === generation.current) setLoading(false); }
  }, [project, assetType]);
  useEffect(() => { void refresh(); return () => { generation.current++; }; }, [refresh, document?.current_revision_id]);
  const open = (item: NarrativeEntity | "new") => {
    setEditing(item); setError(""); pending.current = null;
    setBlock(item === "new" ? document?.revision.blocks[0]?.id ?? "" : item.block_id);
    setName(item === "new" ? "" : item.name);
    setAsset(item === "new" || item.asset_missing ? "" : item.asset_id ?? "");
    setCreate(false); setAssetName(""); setDescription("");
    setRelations(item === "new" ? [] : item.relations.map(({ kind, entity_id }) => ({ kind, entity_id })));
    setAppearances(item === "new" ? [] : item.appearances.map(({ stale: _, ...value }) => value));
  };
  const save = async () => {
    if (!document || !editing || !allSaved || busy) return;
    const body: Omit<EntityInput, "client_mutation_id"> = { document_id: document.id, base_revision_id: document.current_revision_id,
      block_id: block, name, ...(editing === "new" ? {} : { entity_id: editing.entity_id }), relations, appearances,
      ...(create ? { create_text: { name: assetName, description } } : asset ? { asset_id: asset } : {}) };
    const payload = JSON.stringify(body);
    if (pending.current?.payload !== payload) pending.current = { payload, mutation: crypto.randomUUID() };
    setBusy(true); setError("");
    try { await scriptCreationApi.putEntity(project, { ...body, client_mutation_id: pending.current.mutation }); setEditing(null); await refresh(); }
    catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };
  const addAppearance = () => {
    if (episode.startsWith("planned:")) setAppearances([...appearances, { kind: appearanceKind, status: "planned", episode_number: Number(episode.split(":")[1]) }]);
    else {
      const doc = documents.find((item) => item.id === episode);
      if (doc) setAppearances([...appearances, { kind: appearanceKind, status: "written", document_id: doc.id, revision_id: doc.current_revision_id }]);
    }
  };
  return <div className="min-h-0 overflow-y-auto p-4 text-xs text-white/75">
    <p className="mb-3 leading-5 text-white/45">选择人物、场景或道具条目，明确关联已有资产或新建文字记录。文字记录可用于后续制作。</p>
    {!allSaved && <p className="mb-3 text-amber-200">请先保存全部文档，再关联已保存版本。</p>}
    {loading && <p role="status">正在加载资产关联…</p>}
    {error && <div role="alert" className="mb-3 text-amber-200">{error} <button className={action} onClick={() => void refresh()}>重试</button></div>}
    {!assetType && <p>在文档树中选择人物、场景或道具设计。</p>}
    {assetType && <>
      <button className={action + " mb-3 text-[#E5FF5C]"} disabled={!allSaved || loading || busy || !document?.revision.blocks.length} onClick={() => open("new")}>登记设计条目</button>
      {entities.filter((item) => item.document_id === document?.id).map((item) => <div key={item.entity_id} className="mb-3 rounded border border-white/10 p-3">
        <strong className="text-white">{item.name}</strong><p className="mt-1 text-white/45">{item.asset_name ? `关联资产：${item.asset_name}` : item.asset_id ? "已关联资产" : "尚未关联资产"}</p>
        {item.asset_missing && <p className="mt-1 text-red-300">资产已删除或不可用</p>}
        {item.entry_missing && <p className="mt-1 text-red-300">原设计条目已缺失，不能转移到其他条目</p>}
        {item.stale && <p className="mt-1 text-amber-200">设计已更新，确认关联版本</p>}
        {!!item.appearances.length && <p className="mt-1">{item.appearances.map((ref) => `${ref.kind === "first_appearance" ? "首次出场" : "关键场景"} · ${ref.status === "planned" ? `计划第 ${ref.episode_number} 集` : `已写入 ${documents.find((d) => d.id === ref.document_id)?.title ?? "分集"} · 版本 ${ref.revision_id?.slice(0, 8)}`}${ref.stale ? "（正文已更新）" : ""}`).join("；")}</p>}
        {!!item.relations.length && <p className="mt-1 text-white/45">{item.relations.map((rel) => `${rel.kind === "entry" ? "入场人物" : rel.kind === "holding" ? "持有道具" : "关键道具"}：${rel.missing ? `条目缺失（${entities.find((e) => e.entity_id === rel.entity_id)?.name ?? "未知条目"}）` : `${entities.find((e) => e.entity_id === rel.entity_id)?.name ?? "条目缺失"}${rel.stale ? "（待确认）" : ""}`}`).join("；")}</p>}
        <button className={action + " mt-2"} disabled={!allSaved || busy || item.entry_missing} onClick={() => open(item)}>关联 / 确认 {item.name}</button>
      </div>)}
    </>}
    {editing && <section role="dialog" aria-label="关联设计条目" className="mt-3 space-y-3 rounded border border-[#E5FF5C]/25 bg-black/20 p-3">
      <p className="text-[#E5FF5C]">确认当前条目与关联版本</p>
      <label className="block">正文条目<select className={field} aria-label="正文条目" value={block} disabled={editing !== "new"} onChange={(event) => setBlock(event.target.value)}>{document?.revision.blocks.map((item) => <option key={item.id} value={item.id}>{item.markdown.slice(0, 80)}</option>)}</select></label>
      <label className="block">条目名称<input className={field} aria-label="条目名称" value={name} onChange={(event) => setName(event.target.value)} /></label>
      <label className="block"><input type="checkbox" checked={create} onChange={(event) => setCreate(event.target.checked)} /> 新建文字资产记录</label>
      {create ? <><input className={field} aria-label="新资产名称" placeholder="资产名称（必须未使用）" value={assetName} onChange={(event) => setAssetName(event.target.value)} /><textarea className={field} aria-label="文字资产描述" placeholder="文字描述" value={description} onChange={(event) => setDescription(event.target.value)} /></> : <label className="block">选择现有资产<select aria-label="选择现有资产" className={field} value={asset} onChange={(event) => setAsset(event.target.value)}><option value="">保留当前状态</option>{assets.map((item) => <option key={item.asset_id} value={item.asset_id}>{item.name}</option>)}</select></label>}
      {assetType !== "prop" && <div className="space-y-1"><p>条目关系</p>{entities.filter((item) => assetType === "character" ? item.asset_type === "prop" : item.asset_type === "prop" || item.asset_type === "character").map((item) => {
        const kind = assetType === "character" ? "holding" : item.asset_type === "prop" ? "key_prop" : "entry";
        return <label key={item.entity_id} className="block"><input type="checkbox" disabled={item.entry_missing} checked={relations.some((rel) => rel.entity_id === item.entity_id)} onChange={(event) => setRelations(event.target.checked ? [...relations, { kind, entity_id: item.entity_id }] : relations.filter((rel) => rel.entity_id !== item.entity_id))} /> {kind === "holding" ? "持有" : kind === "entry" ? "入场" : "关键道具"} · {item.name}{item.entry_missing ? "（条目缺失）" : ""}</label>;
      })}</div>}
      <div className="space-y-2"><p>出场记录</p><select aria-label="出场类型" className={field} value={appearanceKind} onChange={(event) => setAppearanceKind(event.target.value as EntityAppearance["kind"])}><option value="first_appearance">首次出场</option><option value="critical_scene">关键场景</option></select>
        <select aria-label="出场位置" className={field} value={episode} onChange={(event) => setEpisode(event.target.value)}>{Array.from({ length: Math.max(1, ...documents.map((d) => d.episode_number ?? 1)) + 1 }, (_, i) => <option key={i} value={`planned:${i + 1}`}>计划第 {i + 1} 集</option>)}{documents.filter((d) => d.kind === "episode_script").map((d) => <option key={d.id} value={d.id}>已写入 · {d.title} · 当前保存版本</option>)}</select>
        <button className={action} onClick={addAppearance}>添加出场记录</button>
        {appearances.map((ref, index) => <div key={index}>{ref.kind === "first_appearance" ? "首次出场" : "关键场景"} · {ref.status === "planned" ? `计划第 ${ref.episode_number} 集` : `已写入 · ${documents.find((d) => d.id === ref.document_id)?.title ?? "分集"}`} <button className={action} onClick={() => setAppearances(appearances.filter((_, i) => i !== index))}>移除</button></div>)}
      </div>
      <p className="text-white/40">确认前请核对：当前正文仍描述同一条目；已删除条目不能借同名恢复关联。</p>
      <div className="flex gap-2"><button className={action + " bg-[#E5FF5C] text-black"} disabled={!allSaved || busy || !name.trim() || !block || (create && !assetName.trim())} onClick={() => void save()}>{busy ? "保存中…" : "保存关联"}</button><button className={action} disabled={busy} onClick={() => setEditing(null)}>取消</button></div>
    </section>}
  </div>;
}
