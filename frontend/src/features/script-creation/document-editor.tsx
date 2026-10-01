import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { AlertCircle, Check, CircleDashed, Pencil, Save } from "lucide-react";
import { scriptCreationApi } from "./api";
import type { DocumentDraft, DraftManager } from "./draft-manager";
import type { ConsistencyEvidence, ScriptDocument } from "./types";
import { remarkBriefSettings, remarkDocumentLabels } from "./document-markdown";

type Props = {
  project: string;
  draft: DocumentDraft;
  manager: DraftManager;
  onSelection: (selection: { text: string; start: number; end: number } | null) => void;
  focusEvidence?: ConsistencyEvidence | null;
};

export function DocumentEditor({ project, draft, manager, onSelection, focusEvidence }: Props) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(draft.markdown);
  const [server, setServer] = useState<ScriptDocument | null>(null);
  const [compareError, setCompareError] = useState("");
  const composing = useRef(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const headingId = (offset: number | undefined) => offset === undefined
    ? undefined
    : "heading-" + draft.document.id + "-" + Array.from(draft.markdown.slice(0, offset)).length;

  useEffect(() => { if (!composing.current) setValue(draft.markdown); }, [draft.markdown]);
  useEffect(() => { setServer(null); setCompareError(""); }, [draft.document.id]);
  useEffect(() => { onSelection(null); }, [draft.document.id, draft.document.current_revision_id, draft.markdown]);

  useEffect(() => {
    if (!focusEvidence || focusEvidence.document_id !== draft.document.id ||
        focusEvidence.revision_id !== draft.document.current_revision_id) return;
    setEditing(true);
  }, [focusEvidence, draft.document.id, draft.document.current_revision_id]);
  useEffect(() => {
    if (!editing || !focusEvidence || focusEvidence.document_id !== draft.document.id ||
        focusEvidence.revision_id !== draft.document.current_revision_id || !input.current) return;
    let offset = 0;
    const block = draft.document.revision.blocks.find((item) => {
      if (item.id === focusEvidence.block_id) return true;
      offset += Array.from(item.markdown).length;
      return false;
    });
    if (!block || Array.from(block.markdown).slice(focusEvidence.start, focusEvidence.end).join("") !== focusEvidence.quote) return;
    const codepoints = Array.from(draft.markdown);
    const start = codepoints.slice(0, offset + focusEvidence.start).join("").length;
    const end = codepoints.slice(0, offset + focusEvidence.end).join("").length;
    input.current.focus();
    input.current.setSelectionRange(start, end);
    input.current.scrollIntoView?.({ block: "center" });
  }, [editing, focusEvidence, draft.document.id, draft.document.current_revision_id, draft.markdown]);

  const select = () => {
    const element = input.current;
    if (!element) { onSelection(null); return; }
    onSelection({
      text: element.value.slice(element.selectionStart, element.selectionEnd),
      start: Array.from(element.value.slice(0, element.selectionStart)).length,
      end: Array.from(element.value.slice(0, element.selectionEnd)).length,
    });
  };
  const managerFetchLatest = () => scriptCreationApi.get(project, draft.document.id);
  const compare = async () => {
    setCompareError("");
    setServer(null);
    try { setServer(await managerFetchLatest()); }
    catch { setCompareError("最新版本读取失败，请重试。"); }
  };
  const resolve = async (keepLocal: boolean) => {
    if (!server) return;
    manager.resolveConflict(draft.document.id, server, keepLocal);
    setServer(null);
    if (keepLocal) await manager.flush(draft.document.id);
  };

  return (
    <article className="mx-auto my-7 min-h-[calc(100%-4rem)] w-full max-w-[860px] rounded-xl border border-white/[0.07] bg-[#181A1E] px-6 py-7 shadow-xl shadow-black/20 md:px-10">
      <div className="flex flex-wrap items-center gap-2 border-b border-white/10 pb-4">
        <div className="min-w-0 flex-1"><span className="text-[11px] uppercase tracking-[.17em] text-white/40">创作文档 / {draft.document.title}</span><h1 className="mt-2 truncate text-xl font-semibold">{draft.document.title}</h1></div>
        <span aria-live="polite" className={"inline-flex items-center gap-1 text-xs " + (draft.status === "error" || draft.status === "conflict" ? "text-amber-300" : "text-white/45")}>
          {draft.status === "saved" ? <><Check size={13} />已保存</> : draft.status === "saving" ? <><CircleDashed size={13} />保存中</> : draft.status === "dirty" ? "待保存" : draft.status === "conflict" ? <><AlertCircle size={13} />版本冲突</> : <><AlertCircle size={13} />保存失败</>}
        </span>
        {draft.status === "error" && <button onClick={() => void manager.retry(draft.document.id)} className="rounded border border-amber-300/30 px-2 py-1 text-xs text-amber-200">重试保存</button>}
        {draft.status === "conflict" && <button onClick={() => void compare()} className="rounded border border-amber-300/30 px-2 py-1 text-xs text-amber-200">比较最新版本</button>}
        <button onClick={() => setEditing((current) => !current)} className="inline-flex items-center gap-1 rounded border border-white/15 px-2.5 py-1.5 text-xs hover:border-white/35"><Pencil size={13} />{editing ? "预览 Markdown" : "编辑 Markdown"}</button>
      </div>
      {draft.error && draft.status === "error" && <p role="alert" className="mt-3 text-xs text-amber-200">{draft.error}</p>}
      {compareError && <p role="alert" className="mt-3 text-xs text-amber-200">{compareError}</p>}
      {server && <section aria-label="版本比较" className="mt-4 grid gap-3 rounded border border-amber-300/20 bg-amber-300/5 p-4 text-xs md:grid-cols-2">
        <div><b>我的修改</b><pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap">{draft.markdown}</pre></div>
        <div><b>服务器最新版本</b><pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap">{server.revision.markdown}</pre></div>
        <div className="flex gap-2 md:col-span-2"><button onClick={() => void resolve(true)} className="rounded bg-[#E5FF5C] px-3 py-1.5 text-black">保留我的修改并保存</button><button onClick={() => void resolve(false)} className="rounded border border-white/20 px-3 py-1.5">使用服务器版本</button></div>
      </section>}
      {editing ? <div className="mt-5">
        <textarea ref={input} aria-label="文档 Markdown" value={value} onChange={(event) => { setValue(event.target.value); if (!composing.current) manager.edit(draft.document.id, event.target.value); }} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={(event) => { composing.current = false; setValue(event.currentTarget.value); manager.edit(draft.document.id, event.currentTarget.value); }} onSelect={select} spellCheck={false} className="min-h-[560px] w-full resize-y rounded border border-white/10 bg-[#0D0E10] p-4 font-mono text-[13px] leading-7 text-[#E0E3E7] outline-none focus:border-[#E5FF5C]/40" />
        <div className="mt-2 flex items-center justify-between text-xs text-white/45"><span>编辑内容将自动保存；支持 Markdown 标题、场次和对白。</span><button onClick={() => void manager.flush(draft.document.id)} className="inline-flex items-center gap-1 rounded border border-white/15 px-2.5 py-1.5 text-white/80"><Save size={13} />立即保存</button></div>
      </div> : <div className="mt-6 min-w-0 break-words text-[15px] leading-8 text-[#C9CED6]">
        <ReactMarkdown skipHtml remarkPlugins={[remarkGfm, remarkDocumentLabels, [remarkBriefSettings, { enabled: draft.document.kind === "brief", title: draft.document.title }], remarkBreaks]} components={{
          h1: ({ children }) => <h1 className="mb-5 mt-8 text-2xl font-semibold leading-snug text-white first:mt-0">{children}</h1>,
          h2: ({ node, children }) => <h2 id={headingId(node?.position?.start.offset)} className="mb-4 mt-9 scroll-mt-4 border-t border-white/10 pt-6 text-xl font-semibold leading-snug text-white">{children}</h2>,
          h3: ({ node, children }) => <h3 id={headingId(node?.position?.start.offset)} className="mb-3 mt-6 scroll-mt-4 text-lg font-semibold leading-snug text-white">{children}</h3>,
          h4: ({ children }) => <h4 className="mb-2 mt-5 font-semibold text-white">{children}</h4>,
          h5: ({ children }) => <h5 className="mb-2 mt-4 font-semibold text-white">{children}</h5>,
          h6: ({ children }) => <h6 className="mb-2 mt-4 text-sm font-semibold text-white">{children}</h6>,
          p: ({ children }) => <p className="my-4 leading-8">{children}</p>,
          strong: ({ children }) => <strong className="font-semibold text-[#F1F3F5]">{children}</strong>,
          ul: ({ children }) => <ul className="my-4 list-disc space-y-2 pl-6 marker:text-white/45">{children}</ul>,
          ol: ({ children, start }) => <ol start={start} className="my-4 list-decimal space-y-2 pl-6 marker:text-white/60">{children}</ol>,
          li: ({ children }) => <li className="pl-1 [&>p]:my-1">{children}</li>,
          blockquote: ({ children }) => <blockquote className="my-5 border-l-2 border-[#E5FF5C]/45 bg-white/[0.025] py-1 pl-4 pr-3 text-white/65">{children}</blockquote>,
          hr: () => <hr className="my-8 border-white/10" />,
          a: ({ children, href }) => <a href={href} className="text-[#E5FF5C] underline decoration-[#E5FF5C]/40 underline-offset-4">{children}</a>,
          table: ({ children }) => <div className="my-5 overflow-x-auto rounded-lg border border-white/10"><table className="w-full border-collapse text-left text-sm">{children}</table></div>,
          th: ({ children, style }) => <th style={style} className="border-b border-white/15 bg-white/5 px-4 py-2.5 font-semibold text-white">{children}</th>,
          td: ({ children, style }) => <td style={style} className="border-b border-white/10 px-4 py-2.5 align-top">{children}</td>,
          pre: ({ children }) => <pre className="my-5 overflow-x-auto rounded-lg border border-white/10 bg-black/20 p-4 text-[13px] leading-6 [&>code]:bg-transparent [&>code]:p-0">{children}</pre>,
          code: ({ children, className }) => <code className={"rounded bg-white/[0.06] px-1 py-0.5 font-mono text-[0.9em] " + (className ?? "")}>{children}</code>,
        }}>{draft.markdown}</ReactMarkdown>
      </div>}
    </article>
  );
}
