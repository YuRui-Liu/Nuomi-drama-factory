import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { scriptCreationApi } from "@/features/script-creation/api";
import { sceneContextApi, type SceneContextLink } from "@/features/script-creation/scene-context-api";

interface Source { assetId: string; name: string; documentId: string; title: string; revision: string }
export function SceneContextSource({ project, name }: { project: string; name: string }) {
  const [sources, setSources] = useState<Source[]>([]);
  const [target, setTarget] = useState("");
  const [links, setLinks] = useState<SceneContextLink[]>([]);
  const [selected, setSelected] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refresh, setRefresh] = useState(0);
  const mutation = useRef<{ key: string; id: string } | null>(null);
  useEffect(() => {
    let live = true;
    setLoading(true); setError(""); setSelected(""); setNotice(""); setLinks([]);
    void (async () => {
      const [assets, documents, entities] = await Promise.all([scriptCreationApi.listEntityAssets(project, "scene"), scriptCreationApi.list(project), scriptCreationApi.listEntities(project)]);
      const targetId = assets.find((asset) => asset.name === name)?.asset_id;
      if (!targetId) throw new Error("此场景尚无资产编号，请刷新资产列表后重试。");
      const docs = documents.filter((doc) => doc.kind === "scenes");
      const histories = await Promise.all(docs.map((doc) => scriptCreationApi.listAssetExtractions(project, "scene", doc.id)));
      const choices = new Map<string, Source>();
      docs.forEach((doc, index) => {
        const assetIds = [
          ...histories[index].filter((run) => run.status === "committed" && run.source_revision_id === doc.current_revision_id).flatMap((run) => run.result?.map((item) => item.asset_id) ?? []),
          ...entities.filter((entity) => entity.asset_type === "scene" && entity.document_id === doc.id && entity.confirmed_revision === doc.current_revision_id && entity.selected_revision === doc.current_revision_id && !entity.asset_missing && !entity.entry_missing).flatMap((entity) => entity.asset_id ? [entity.asset_id] : []),
        ];
        for (const assetId of assetIds) {
          const asset = assets.find((item) => item.asset_id === assetId);
          if (asset && assetId !== targetId) choices.set(`${assetId}/${doc.id}`, { assetId, name: asset.name, documentId: doc.id, title: doc.title, revision: doc.current_revision_id });
        }
      });
      const existing = await sceneContextApi.list(project, targetId);
      if (live) { setTarget(targetId); setSources([...choices.values()]); setLinks(existing); }
    })().catch((cause) => { if (live) setError(cause instanceof Error ? cause.message : "读取来源失败"); }).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [project, name, refresh]);
  const source = sources.find((item) => `${item.assetId}/${item.documentId}` === selected);
  async function confirm() {
    if (!source || !target || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const latest = await scriptCreationApi.get(project, source.documentId);
      if (latest.current_revision_id !== source.revision) throw new Error("来源场景表已过期，请刷新并重新确认已导入的来源。");
      const body = { source_asset_id: source.assetId, target_asset_ids: [target], document_id: source.documentId, base_revision_id: source.revision };
      const key = JSON.stringify(body);
      if (mutation.current?.key !== key) mutation.current = { key, id: crypto.randomUUID() };
      await sceneContextApi.associate(project, { ...body, client_mutation_id: mutation.current.id });
      setLinks(await sceneContextApi.list(project, target));
      setNotice(`已关联「${source.name}」的创作设定。`);
      mutation.current = null;
    } catch (cause) { setError(cause instanceof Error ? cause.message : "关联失败"); }
    finally { setBusy(false); }
  }
  const groups = [...new Map(links.map((link) => [`${link.source_asset_id}/${link.document_id}/${link.source_revision_id}`, link])).values()];
  return <section aria-label="创作设定来源" className="mt-5 space-y-3 border-t pt-4">
    <h4 className="text-sm font-medium">创作设定来源</h4>
    <p className="text-xs text-muted-foreground">为此独立空间复用已确认的作者场景设定。不新建场景，也不将不同空间合并为昼夜变体。</p>
    {groups.map((link) => <p key={`${link.source_asset_id}/${link.document_id}/${link.source_revision_id}`} className="text-xs">{link.source_name} · 来源版本 {link.source_revision_id} · {link.stale ? "已过期，请先重新提取确认来源，再关联新版本" : "已关联"}</p>)}
    {loading ? <p role="status" className="text-xs">正在读取创作来源…</p> : <>
      <label className="grid gap-1 text-sm">来源场景<select className="rounded border bg-background p-2" value={selected} disabled={busy} onChange={(event) => setSelected(event.target.value)}><option value="">请选择已关联创作设定的场景</option>{sources.map((item) => <option key={`${item.assetId}/${item.documentId}`} value={`${item.assetId}/${item.documentId}`}>{item.name} · {item.title}</option>)}</select></label>
      {source && <p className="text-xs text-muted-foreground">将复用「{source.title}」版本 {source.revision} 中已确认关联到「{source.name}」的完整来源设定。</p>}
      {!sources.length && !error && <p className="text-xs text-muted-foreground">暂无可用来源，请先从创作场景表提取并确认入库。</p>}
      <Button variant="outline" size="sm" disabled={!source || busy || !!error} onClick={() => void confirm()}>确认复用来源设定</Button>
    </>}
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    {notice && <p role="status" className="text-xs">{notice}</p>}
    <Button variant="ghost" size="sm" disabled={busy || loading} onClick={() => setRefresh((value) => value + 1)}>刷新来源</Button>
  </section>;
}
