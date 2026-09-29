import { useEffect, useRef, useState } from "react";
import { scriptCreationApi } from "./api";
import type { ScriptDocument, ScriptRevision } from "./types";

const message = (error: unknown) => error instanceof Error ? error.message : "读取版本失败";

export function RevisionHistory({ project, document, saved, onApplied }: {
  project: string; document: ScriptDocument; saved: boolean; onApplied: () => Promise<unknown> | void;
}) {
  const [revisions, setRevisions] = useState<ScriptRevision[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useRef(0);
  const mutation = useRef<{ target: string; base: string; id: string } | null>(null);
  useEffect(() => {
    const sequence = ++request.current;
    setRevisions([]); setSelectedId(null); setError("");
    void scriptCreationApi.revisions(project, document.id).then((items) => {
      if (request.current !== sequence) return;
      setRevisions([...items].reverse());
      setSelectedId(document.current_revision_id);
    }).catch((cause) => { if (request.current === sequence) setError(message(cause)); });
    return () => { request.current++; };
  }, [project, document.id, document.current_revision_id]);
  const selected = revisions.find((item) => item.id === selectedId);
  const restore = async () => {
    if (!saved || !selected || selected.id === document.current_revision_id) return;
    const previous = mutation.current;
    const id = previous?.target === selected.id && previous.base === document.current_revision_id
      ? previous.id : crypto.randomUUID();
    mutation.current = { target: selected.id, base: document.current_revision_id, id };
    setBusy(true); setError("");
    try {
      await scriptCreationApi.restore(project, document.id, selected.id, document.current_revision_id, id);
      mutation.current = null;
      await onApplied();
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };
  return <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 text-xs text-white/75">
    <div className="font-semibold text-[#E5FF5C]">版本历史</div>
    <p className="text-white/50">当前版本 {document.current_revision_id.slice(0, 8)} · {document.title}</p>
    {!saved && <p className="text-amber-200">先保存并解决文档冲突，才能恢复历史版本。</p>}
    {error && <p role="alert" className="text-rose-200">{error}</p>}
    <div className="space-y-1">{revisions.map((revision) => <button key={revision.id}
      onClick={() => setSelectedId(revision.id)} aria-current={selectedId === revision.id ? "true" : undefined}
      className={"w-full rounded border p-2 text-left " + (selectedId === revision.id ? "border-[#E5FF5C]/50 bg-[#E5FF5C]/5" : "border-white/10")}>
      <span>{new Date(revision.created_at).toLocaleString()}</span><span className="ml-2 text-white/40">{revision.id.slice(0, 8)}</span>
      {revision.restored_from_revision_id && <span className="block text-white/45">恢复自 {revision.restored_from_revision_id.slice(0, 8)}</span>}
    </button>)}</div>
    {selected && <section className="rounded border border-white/10 p-3"><div className="mb-2 font-semibold">版本差异</div>
      <div className="space-y-2"><div><p className="text-white/45">选中版本</p><pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-black/20 p-2">{selected.markdown}</pre></div>
      <div><p className="text-white/45">当前版本</p><pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-black/20 p-2">{document.revision.markdown}</pre></div></div>
      <button disabled={!saved || busy || selected.id === document.current_revision_id} onClick={() => void restore()}
        className="mt-3 w-full rounded bg-[#E5FF5C] px-3 py-2 font-semibold text-black disabled:opacity-35">恢复所选版本</button>
    </section>}
  </div>;
}
