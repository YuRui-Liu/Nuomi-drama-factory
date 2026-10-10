import { useEffect, useState, useRef } from "react";
import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import { Button } from "@/components/ui/button";

type Event = { dialogue_id: string; speaker: string; text: string; start: number; end: number;
  source: "estimated" | "manual" | "aligned"; review_status: "needs_review" | "approved"; audio_fingerprint: string };
type Track = { duration: number; timing_basis: "actual" | "estimated"; audio_fingerprint: string; source_durations: number[]; events: Event[]; timing_notes?: string[]; alignment_issues?: {kind: string; message: string; dialogue_id?: string; recognized_text?: string}[] };
type Document = { revision: string; track: Track };

export function CaptionEditor({ project, episode, currentTime, onSeek, previewReady }: {
  project: string; episode: number; currentTime: number; previewReady: boolean; onSeek: (time: number) => void;
}) {
  const [document, setDocument] = useState<Document | null>(null);
  const [revision, setRevision] = useState("unsaved");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const requestSequence = useRef(0);
  const url = p`api/v1/projects/${project}/episodes/${episode}/captions`;
  async function request(action: "load" | "save" | "rebuild" | "align") {
    const sequence = ++requestSequence.current;
    setBusy(true); setError("");
    try {
      const response = action === "load" ? api.get(url) : action === "save"
        ? api.put(url, { json: document }) : api.post(`${url}/${action}`, { json: { revision }, timeout: action === "align" ? false : 30000 });
      const result = await response.json<{ data: Document }>();
      if (sequence !== requestSequence.current) return;
      setDocument(result.data); setRevision(result.data.revision); setDirty(false);
    } catch (err) {
      const failure = err as { data?: unknown; response?: Response } | null;
      let data = failure?.data;
      if (data === undefined && failure?.response) {
        // ky supplies parsed HTTPError.data and may already have consumed the body.
        try { data = await failure.response.clone().json(); } catch { /* retain fallback */ }
      }
      const body = data && typeof data === "object" ? data as {detail?: unknown; revision?: unknown} : null;
      if (sequence !== requestSequence.current) return;
      setError(typeof body?.detail === "string" ? body.detail : err instanceof Error ? err.message : "字幕读取失败");
      if (typeof body?.revision === "string") setRevision(body.revision);
    } finally { if (sequence === requestSequence.current) setBusy(false); }
  }
  useEffect(() => {
    setDocument(null); setDirty(false); setConfirmDiscard(false); setRevision("unsaved"); void request("load");
    return () => { requestSequence.current++; };
  }, [url]); // eslint-disable-line react-hooks/exhaustive-deps
  function edit(index: number, changes: Partial<Event>) {
    if (!document) return;
    setDocument({ ...document, track: { ...document.track, events: document.track.events.map((event, i) =>
      i === index ? { ...event, source: "manual", review_status: "needs_review", ...changes } : event) } });
    setDirty(true);
  }
  const active = document?.track.events.find(event => currentTime >= event.start && currentTime < event.end);
  const pending = document?.track.events.filter(event => event.review_status !== "approved").length ?? 0;
  return <section aria-label="字幕校时" className="space-y-3 rounded-lg border border-white/10 p-4">
    <h2 className="text-sm font-semibold">字幕校时 · {pending ? `${pending} 条待复核` : "人工复核字幕"}</h2>
    <p className="text-xs text-muted-foreground">估算草稿不代表声音已对齐。逐条试听、调整起止时间并确认；保存后重新合成才能更新画面内字幕。</p>
    <p className="text-xs text-muted-foreground">从原声对齐使用本机已缓存的语音模型，按当前采用片段创建待复核草稿并保留旧版历史；不会自动确认或改写剧本对白。</p>
    {!previewReady && <p className="text-xs text-amber-300">请先生成并加载测试预览，再试听和确认字幕校时。可先关闭字幕合成一版预览。</p>}
    <div aria-label="当前字幕" className="min-h-12 whitespace-pre-line rounded bg-black/60 p-2 text-center">{active?.text ?? "当前无字幕"}</div>
    {error && <p role="alert" className="text-sm text-amber-300">{error}</p>}
    <div className="flex flex-wrap gap-2">
      <Button size="sm" variant="outline" disabled={busy || dirty} onClick={() => void request("load")}>重新载入</Button>
      <Button size="sm" disabled={busy || !document || !dirty} onClick={() => void request("save")}>保存字幕</Button>
      <Button size="sm" variant="outline" disabled={busy || dirty} onClick={() => void request("rebuild")}>重建估算草稿（保留历史）</Button>
      <Button size="sm" variant="outline" disabled={busy || dirty} onClick={() => void request("align")}>从原声对齐</Button>
      {dirty && <Button size="sm" variant="outline" disabled={busy} onClick={() => setConfirmDiscard(true)}>放弃修改并重新载入</Button>}
    </div>
    {busy && <p role="status" className="text-xs text-muted-foreground">正在处理字幕；原声对齐可能需要数分钟。</p>}
    {document?.track.timing_notes?.map((note, i) => <p key={`note-${i}`} className="text-xs text-amber-300">{note}</p>)}
    {document?.track.alignment_issues?.map((issue, i) => <p key={`issue-${i}`} className="text-xs text-amber-300">{issue.dialogue_id ? `${issue.dialogue_id}：` : ""}{issue.message}{issue.recognized_text ? `（${issue.recognized_text}）` : ""}</p>)}
    {confirmDiscard && <div role="alertdialog" aria-label="确认放弃未保存字幕" className="space-y-2 rounded border border-amber-400/30 p-3">
      <p className="text-sm">将放弃本页未保存的字幕修改，并载入最新版本。是否继续？</p>
      <div className="flex gap-2">
        <Button size="sm" variant="outline" onClick={() => setConfirmDiscard(false)}>保留修改</Button>
        <Button size="sm" disabled={busy} onClick={() => { setConfirmDiscard(false); setDirty(false); setDocument(null); void request("load"); }}>确认放弃并载入</Button>
      </div>
    </div>}
    {dirty && <p className="text-xs text-amber-300">有未保存修改</p>}
    <div className="max-h-96 space-y-3 overflow-y-auto">
      {document?.track.events.map((event, index) => <fieldset key={event.dialogue_id} disabled={busy} className="space-y-2 rounded border border-white/10 p-3">
        <legend className="px-1 text-xs">{index + 1} · {event.source === "estimated" ? "估算草稿" : event.source === "aligned" ? "原声对齐" : "人工校时"} · {event.review_status === "approved" ? "已确认" : "待复核"}</legend>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <label>开始秒 <input aria-label={`第${index + 1}条开始秒`} className="w-20 rounded bg-white/5 p-1" type="number" step="0.01" min="0" value={event.start} onChange={e => edit(index, { start: Number(e.target.value) })} /></label>
          <label>结束秒 <input aria-label={`第${index + 1}条结束秒`} className="w-20 rounded bg-white/5 p-1" type="number" step="0.01" min="0" value={event.end} onChange={e => edit(index, { end: Number(e.target.value) })} /></label>
          <label>角色 <input className="w-24 rounded bg-white/5 p-1" value={event.speaker} onChange={e => edit(index, { speaker: e.target.value })} /></label>
          <Button size="sm" variant="outline" disabled={!previewReady} onClick={() => onSeek(event.start)}>试听此条</Button>
        </div>
        <textarea aria-label={`第${index + 1}条字幕`} className="w-full rounded bg-white/5 p-2 text-sm" rows={2} value={event.text} onChange={e => edit(index, { text: e.target.value })} />
        <Button size="sm" variant="outline" disabled={!previewReady || document.track.timing_basis !== "actual"} onClick={() => edit(index, { review_status: "approved" })}>确认此条校时</Button>
      </fieldset>)}
    </div>
  </section>;
}
