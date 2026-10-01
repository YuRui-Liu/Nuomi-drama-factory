import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { KnowledgeGraphNode, KnowledgeGraphSnapshot } from "@/lib/queries/ingest";
import type { KnowledgeGraphUpdate } from "@/lib/queries/knowledge-graph";

type Draft = { kind: "node" | "edge"; id: string; revision: string; name: string; properties: string; original: Record<string, unknown>; source?: string; target?: string };
const fieldClass = "w-full rounded-md border border-white/15 bg-background p-2 text-xs text-foreground";

export function KnowledgeGraphEditor({ graph, node, onSave, onEditingChange }: {
  graph: KnowledgeGraphSnapshot;
  node: KnowledgeGraphNode;
  onSave: (update: KnowledgeGraphUpdate) => Promise<unknown>;
  onEditingChange?: (editing: boolean) => void;
}) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const relations = graph.edges.filter((edge) => edge.source === node.id || edge.target === node.id);
  const edit = (value: Draft | null) => { setDraft(value); setError(""); setSaved(false); onEditingChange?.(value != null); };
  const save = async () => {
    if (!draft) return;
    setError(""); setBusy(true);
    try {
      if (!draft.name.trim()) throw new Error("名称不能为空");
      let properties: Record<string, unknown>;
      try {
        const value: unknown = JSON.parse(draft.properties);
        if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
        properties = value as Record<string, unknown>;
      } catch { throw new Error("属性必须是有效的 JSON 对象"); }
      // PATCH uses null as a deletion marker, so removed keys are explicit.
      for (const key of Object.keys(draft.original)) if (!(key in properties)) properties[key] = null;
      await onSave(draft.kind === "node"
        ? { revision: draft.revision, node_updates: [{ id: draft.id, label: draft.name.trim(), properties }] }
        : { revision: draft.revision, edge_updates: [{ id: draft.id, relation: draft.name.trim(), source: draft.source, target: draft.target, properties }] });
      setDraft(null); onEditingChange?.(false); setSaved(true);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败，请重试"); }
    finally { setBusy(false); }
  };

  return <div className="mt-4 space-y-3 border-t border-white/10 pt-4">
    <p className="text-[11px] leading-5 text-muted-foreground">图谱编辑独立保存，不会修改剧本或生产资产。</p>
    {draft ? <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <label className="block space-y-1 text-xs">{draft.kind === "node" ? "节点名称" : "关系名称"}
        <input className={fieldClass} value={draft.name} maxLength={200} disabled={busy} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
      </label>
      {draft.kind === "edge" && <>
        {(["source", "target"] as const).map((field) => <label key={field} className="block space-y-1 text-xs">{field === "source" ? "关系起点" : "关系终点"}
          <select className={fieldClass} value={draft[field]} disabled={busy} onChange={(event) => setDraft({ ...draft, [field]: event.target.value })}>
            {graph.nodes.map((item) => <option key={item.id} value={item.id}>{item.label} ({item.type})</option>)}
          </select>
        </label>)}
      </>}
      <label className="block space-y-1 text-xs">{draft.kind === "node" ? "节点属性（JSON）" : "关系属性（JSON）"}
        <textarea rows={7} className={`${fieldClass} font-mono`} value={draft.properties} disabled={busy} onChange={(event) => setDraft({ ...draft, properties: event.target.value })} />
      </label>
      {error && <p role="alert" className="break-words text-xs text-amber-300">{error}</p>}
      <div className="flex gap-2">
        <Button size="sm" type="submit" disabled={busy}>{busy ? "保存中…" : draft.kind === "node" ? "保存节点" : "保存关系"}</Button>
        <Button size="sm" type="button" variant="ghost" disabled={busy} onClick={() => edit(null)}>取消</Button>
      </div>
    </form> : <>
      {saved && <p role="status" className="text-xs text-emerald-300">已保存</p>}
      <Button type="button" variant="outline" size="sm" onClick={() => edit({ kind: "node", id: node.id, revision: graph.revision!, name: node.label, properties: JSON.stringify(node.properties, null, 2), original: node.properties })}>编辑节点</Button>
      {relations.map((edge) => <Button key={edge.id} type="button" className="w-full justify-start truncate" variant="ghost" size="sm" aria-label={`编辑关系 ${edge.relation}`} onClick={() => edit({ kind: "edge", id: edge.id, revision: graph.revision!, name: edge.relation, properties: JSON.stringify(edge.properties, null, 2), original: edge.properties, source: edge.source, target: edge.target })}>
        编辑关系 {edge.relation} · {graph.nodes.find((item) => item.id === (edge.source === node.id ? edge.target : edge.source))?.label}
      </Button>)}
    </>}
  </div>;
}
