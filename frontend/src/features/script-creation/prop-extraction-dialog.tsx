import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { scriptCreationApi } from "./api";
import type { AssetExtractionRun, ScriptDocument, NarrativeAssetType, AssetExtractionCandidate } from "./types";

const message = (error: unknown) => error instanceof Error ? error.message : "读取或提取失败，请重试";
const isExclusionSummary = (warning: string) => warning.startsWith("以下条目未通过来源校验，已排除：");

function sourceSection(document: ScriptDocument | undefined, ids: string[]) {
  const blocks = document?.revision.blocks ?? [];
  const indices = new Set<number>();
  for (const id of ids) {
    const start = blocks.findIndex((block) => block.id === id);
    if (start < 0) continue;
    indices.add(start);
    const level = blocks[start].markdown.match(/^\s{0,3}(#{1,6})\s/)?.[1].length;
    if (!level) continue;
    for (let index = start + 1; index < blocks.length; index++) {
      const next = blocks[index].markdown.match(/^\s{0,3}(#{1,6})\s/)?.[1].length;
      if (next && next <= level) break;
      indices.add(index);
    }
  }
  return [...indices].sort((a, b) => a - b).map((index) => blocks[index].markdown).join("\n\n");
}

interface ExtractionDialogProps {
  project: string; document?: ScriptDocument; allSaved?: boolean; onClose: () => void; onImported?: () => void;
}
export function PropExtractionDialog(props: ExtractionDialogProps) {
  return <AssetExtractionDialog {...props} assetType="prop" />;
}

export function AssetExtractionDialog({ project, assetType, document, allSaved = true, onClose, onImported }: ExtractionDialogProps & { assetType: NarrativeAssetType }) {
  const label = { character: "人物", scene: "场景", prop: "道具" }[assetType];
  const documentKind = { character: "people", scene: "scenes", prop: "props" }[assetType];
  const visualPrompt = (item: AssetExtractionCandidate) => (assetType === "character" ? [item.fields?.face_prompt, item.fields?.appearance_details, item.fields?.body_type].filter(Boolean).join("\n") : assetType === "scene" ? item.fields?.environment_prompt : item.fields?.visual_prompt ?? item.visual_prompt) ?? "";
  const missingCharacterAppearance = "源表未明确外貌；保留已有造型，可在角色造型室查看或继续设计。";
  const [documents, setDocuments] = useState<ScriptDocument[]>(document ? [document] : []);
  const [documentId, setDocumentId] = useState(document?.id ?? "");
  const [run, setRun] = useState<AssetExtractionRun | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [historyReady, setHistoryReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const request = useRef<{ payload: string; id: string } | null>(null);
  const source = document ?? documents.find((item) => item.id === documentId);
  const pending = run?.status === "pending" || run?.status === "running";
  const stale = run?.status === "needs_rebase" || Boolean(run && source && source.current_revision_id !== run.source_revision_id);
  const hasRawOutput = Boolean(run?.raw_output && Object.keys(run.raw_output).length);
  const rejected = run?.rejected_candidates ?? [];
  const legacyExclusionSummaries = rejected.length ? [] : [...new Set(run?.candidates.flatMap((item) => item.warnings.filter(isExclusionSummary)) ?? [])];
  const canRevalidate = hasRawOutput && (run?.status === "failed" || (run?.status === "ready" && rejected.length > 0));
  const accept = (next: AssetExtractionRun) => {
    setRun(next);
    setSelected(assetType === "prop" ? [] : next.candidates.filter((item) => assetType !== "scene" || (item.action === "reuse" && !!item.existing_asset_id)).map((item) => item.id));
  };
  const mutationId = (payload: unknown) => {
    const serialized = JSON.stringify(payload);
    if (request.current?.payload !== serialized) request.current = { payload: serialized, id: crypto.randomUUID() };
    return request.current.id;
  };
  useEffect(() => {
    if (document) { setDocuments([document]); setDocumentId(document.id); return; }
    let live = true;
    setLoading(true); setError("");
    scriptCreationApi.list(project).then((items) => {
      if (!live) return;
      const props = items.filter((item) => item.kind === documentKind);
      setDocuments(props); setDocumentId((previous) => props.some((item) => item.id === previous) ? previous : props[0]?.id ?? "");
      if (!props.length) setLoading(false);
    }).catch((cause) => { if (live) { setError(message(cause)); setLoading(false); } });
    return () => { live = false; };
  }, [project, document, documentKind, retry]);
  useEffect(() => {
    if (!documentId) return;
    let live = true;
    setLoading(true); setHistoryReady(false); setRun(null); setSelected([]); setError("");
    (assetType === "prop" ? scriptCreationApi.listPropExtractions(project, documentId) : scriptCreationApi.listAssetExtractions(project, assetType, documentId)).then((items) => {
      if (live) { setHistoryReady(true); if (items[0]) accept(items[0]); }
    }).catch((cause) => { if (live) setError(message(cause)); }).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [project, assetType, documentId, retry]);
  useEffect(() => {
    if (!pending || !run) return;
    let live = true;
    const timer = window.setTimeout(() => {
      (assetType === "prop" ? scriptCreationApi.getPropExtraction(project, run.id) : scriptCreationApi.getAssetExtraction(project, assetType, run.id)).then((next) => { if (live) accept(next); })
        .catch((cause) => { if (live) setError(message(cause)); });
    }, 1000);
    return () => { live = false; window.clearTimeout(timer); };
  }, [project, assetType, run, pending]);
  async function start() {
    if (!source || busy || pending || !allSaved || !historyReady) return;
    setBusy(true); setError("");
    try {
      const latest = await scriptCreationApi.get(project, source.id);
      setDocuments((items) => items.map((item) => item.id === latest.id ? latest : item));
      if (document && latest.current_revision_id !== document.current_revision_id) throw new Error(`${label}表已更新，请刷新创作文档后再提取`);
      const body = { document_id: latest.id, base_revision_id: latest.current_revision_id };
      const requestBody = { ...body, client_mutation_id: mutationId(body) };
      accept(await (assetType === "prop" ? scriptCreationApi.startPropExtraction(project, requestBody) : scriptCreationApi.startAssetExtraction(project, assetType, requestBody)));
      request.current = null;
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  }
  async function confirm() {
    if (!run || run.status !== "ready" || !source || stale || !allSaved || busy || !selected.length) return;
    setBusy(true); setError("");
    try {
      const latest = await scriptCreationApi.get(project, source.id);
      setDocuments((items) => items.map((item) => item.id === latest.id ? latest : item));
      if (latest.current_revision_id !== run.source_revision_id) {
        setRun({ ...run, status: "needs_rebase" });
        throw new Error(`${label}表已更新，请重新提取后确认`);
      }
      const body = { base_revision_id: run.source_revision_id, candidate_ids: selected };
      const requestBody = { ...body, client_mutation_id: mutationId({ run: run.id, ...body }) };
      accept(await (assetType === "prop" ? scriptCreationApi.confirmPropExtraction(project, run.id, requestBody) : scriptCreationApi.confirmAssetExtraction(project, assetType, run.id, requestBody)));
      request.current = null; onImported?.();
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  }
  async function revalidate() {
    if (!run || !source || !canRevalidate || stale || !allSaved || busy) return;
    setBusy(true); setError("");
    try {
      const latest = await scriptCreationApi.get(project, source.id);
      setDocuments((items) => items.map((item) => item.id === latest.id ? latest : item));
      if (latest.current_revision_id !== run.source_revision_id) {
        setRun({ ...run, status: "needs_rebase" });
        throw new Error(`${label}表已更新，不能重新校验旧结果，请重新提取`);
      }
      accept(await scriptCreationApi.revalidateAssetExtraction(project, run.id));
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  }
  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}><DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
    <DialogHeader><DialogTitle>从创作{label}表提取</DialogTitle></DialogHeader>
    <p className="text-sm text-muted-foreground">读取已保存{label}表，提取独立{label}资产。确认前不会写入{label}库；同名资产复用并保留已有内容。</p>
    {assetType === "prop" && <p className="text-sm text-amber-600">请主动选择需独立复用的关键道具。零件和背景陈设无需单独建库；默认不选中任何道具，完整来源上下文仍保留。</p>}
    {assetType === "scene" && <p className="text-sm text-amber-600">优先复用已有场景。同一空间的昼夜、天气、光线或损坏状态请使用变体；确需独立空间时再勾选新建。默认仅选择已匹配的复用项。</p>}
    {!document && !!documents.length && <label className="grid gap-1 text-sm">来源{label}表<select aria-label={`来源${label}表`} className="rounded border bg-background p-2" value={documentId} disabled={busy || pending} onChange={(event) => setDocumentId(event.target.value)}>{documents.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>}
    {source && <p className="text-xs text-muted-foreground">来源：{source.title} · 保存版本 {source.current_revision_id}</p>}
    {!allSaved && <p role="alert">请先保存全部创作文档，再提取或确认。</p>}
    {loading && <p role="status">正在读取已保存{label}表…</p>}
    {!loading && !documents.length && <p>尚无创作{label}表，请先在剧本创作中生成或保存{label}表。</p>}
    {error && <div role="alert"><p>{error}</p><Button variant="outline" size="sm" disabled={busy} onClick={() => setRetry((value) => value + 1)}>重新读取</Button></div>}
    {pending && <p role="status">正在提取独立{label}…关闭后可再次打开查看结果。</p>}
    {run?.status === "failed" && <p role="alert">提取失败：{run.error || "请重试"}</p>}
    {(run?.status === "failed" || rejected.length > 0) && !hasRawOutput && <p className="text-sm text-muted-foreground">本次任务未保存原始模型结果，无法免费恢复；重新提取会再次调用模型。</p>}
    {!!rejected.length && <section aria-label="未导入条目" className="space-y-2 rounded border border-amber-500/30 p-3 text-sm">
      <p>已排除 {rejected.length} 项，未加入入库候选：</p>
      {rejected.slice(0, 3).map((item, index) => <p key={index} className="text-xs text-amber-600">{item.name}：{item.reason.split("（")[0].slice(0, 120)}</p>)}
      <details><summary className="cursor-pointer text-xs">查看全部排除原因与原文</summary><div className="mt-2 space-y-3">{rejected.map((item, index) => <div key={index} className="space-y-1 text-xs"><strong>{item.name}</strong><p className="whitespace-pre-wrap break-words">{item.reason}</p><blockquote className="whitespace-pre-wrap border-l-2 pl-2 text-muted-foreground">来源条目 {item.source_block_id}：{item.evidence}</blockquote></div>)}</div></details>
    </section>}
    {legacyExclusionSummaries.map((warning) => <p key={warning} className="text-xs text-amber-600">{warning}</p>)}
    {stale && <p role="alert">来源{label}表已过期，请重新提取后确认。</p>}
    {run?.status === "ready" && <>
      {run.candidates.some((item) => !visualPrompt(item).trim()) && <p className="text-sm text-amber-600">{assetType === "character" ? "部分人物源表未明确外貌；复用不会覆盖已有造型，未定造型可在角色造型室设计。" : `部分${label}缺少明确外观描述，提取完成不代表可直接出图。请核对下方标记；入库后仍需补充生图信息。`}</p>}
      <div className="flex items-center gap-2 text-sm"><span>共 {run.candidates.length} 项 · 已选 {selected.length} 项</span><Button size="sm" variant="outline" disabled={busy || stale} onClick={() => setSelected(run.candidates.map((item) => item.id))}>全选</Button><Button size="sm" variant="outline" disabled={busy} onClick={() => setSelected([])}>取消全选</Button></div>
      {run.candidates.map((item) => <section key={item.id} className="space-y-2 rounded border p-3 text-sm">
        <label className="flex gap-2 font-medium"><input type="checkbox" aria-label={`选择 ${item.name}`} checked={selected.includes(item.id)} disabled={busy || stale} onChange={(event) => setSelected((old) => event.target.checked ? [...old, item.id] : old.filter((id) => id !== item.id))} />{item.name}</label>
        <p className="text-xs text-muted-foreground">{item.action === "reuse" ? `复用已有：${item.existing_name ?? item.name}（保留已有内容）` : `新建独立${label}`} · {assetType === "character" ? [item.fields?.gender, item.fields?.age_group].filter(Boolean).join(" · ") : assetType === "scene" ? [item.fields?.scene_type, item.fields?.time_of_day].filter(Boolean).join(" · ") : item.fields?.prop_type ?? item.prop_type}</p>
        {assetType === "character" && <p className="text-xs text-muted-foreground">人物设定将作为形象、身份与声线设计的业务参考，不自动改变已确认造型或声线。</p>}
        {!visualPrompt(item).trim() && <p className="text-xs font-medium text-amber-600">{assetType === "character" ? missingCharacterAppearance : item.action === "reuse" ? "提取内容缺少生图信息，请核对已有资产的外观描述" : "待补充生图信息 · 不能直接出图"}</p>}
        <p className="line-clamp-3 whitespace-pre-wrap break-words">{item.description}</p>
        <p className="whitespace-pre-wrap"><strong>{assetType === "character" ? "人物外观：" : assetType === "scene" ? "环境描述：" : "生图描述："}</strong>{visualPrompt(item) || (assetType === "character" ? "源表未明确描述（不代表已有造型不可用）" : "缺少外观描述，入库后需补充才能生图")}</p>
        <blockquote className="whitespace-pre-wrap border-l-2 pl-2 text-xs text-muted-foreground">来源条目 {(item.source_block_ids ?? [item.source_block_id]).join("、")}：{item.evidence}</blockquote>
        <details className="rounded border p-2"><summary className="cursor-pointer text-xs">{assetType === "character" ? "完整人物设定与来源条目" : `完整${label}设定与来源条目`}</summary><p className="mt-2 whitespace-pre-wrap break-words text-xs">{item.description}</p><p className="mt-2 whitespace-pre-wrap break-words text-xs text-muted-foreground">{stale ? "来源版本已变化；请重新提取查看完整原文。" : sourceSection(source, item.source_block_ids ?? [item.source_block_id]) || item.description}</p></details>
        {item.warnings.filter((warning) => !isExclusionSummary(warning)).map((warning) => <p key={warning} className="text-xs text-amber-600">{assetType === "character" && warning === "缺少明确外观描述，入库后需补充" ? missingCharacterAppearance : warning}</p>)}
      </section>)}
    </>}
    {run?.status === "committed" && <div role="status" className="space-y-1 text-sm"><p>已入库：新建 {run.result?.filter((item) => item.action === "created").length ?? 0} 项，复用 {run.result?.filter((item) => item.action === "reused").length ?? 0} 项。</p>{run.result?.map((item) => <p key={item.candidate_id}>{item.name} · 已关联来源条目 {item.source_block_id}</p>)}</div>}
    <p className="text-xs text-muted-foreground">提取或重新提取会调用模型，可能产生模型费用；重新校验只检查已保存结果，不调用模型。</p>
    <DialogFooter><Button variant="outline" disabled={busy} onClick={onClose}>关闭</Button>{canRevalidate && <Button variant="outline" disabled={busy || stale || !allSaved} onClick={() => void revalidate()}>重新校验已有结果（不调用模型）</Button>}<Button variant="outline" disabled={loading || !historyReady || busy || pending || !source || !allSaved} onClick={() => void start()}>{busy ? "处理中…" : run ? "重新提取（调用模型）" : `提取独立${label}`}</Button>{run?.status === "ready" && <Button disabled={busy || stale || !allSaved || !selected.length} onClick={() => void confirm()}>确认入库 {selected.length} 项（新建 {run.candidates.filter((item) => selected.includes(item.id) && item.action === "create").length} · 复用 {run.candidates.filter((item) => selected.includes(item.id) && item.action === "reuse").length}）</Button>}</DialogFooter>
  </DialogContent></Dialog>;
}
