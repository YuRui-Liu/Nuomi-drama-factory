import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import { AlertCircle, ChevronDown, ChevronRight, FilePlus2, FileText, Import, Loader2, MessageSquare, Plus, Settings2, X } from "lucide-react";
import { useEpisodeImports } from "@/lib/queries/ingest";
import { scriptCreationApi } from "./api";
import { DraftManager } from "./draft-manager";
import { GenerationPanel } from "./generation-panel";
import { readRecovery, writeRecovery } from "./draft-recovery";
import { DocumentEditor } from "./document-editor";
import { treeForDocuments } from "./document-tree";
import type { TreeNode } from "./document-tree";
import { decodeBriefSettings, defaultSettings, encodeBriefSettings } from "./settings";
import { ScriptSetter } from "./script-setter";
import { episodeScriptTemplate, starterDocuments } from "./templates";
import type { ScriptDocument, ScriptSettings } from "./types";

function errorMessage(error: unknown) { return error instanceof Error ? error.message : "操作失败，请重试"; }

export function ScriptWorkspace({ project }: { project: string }) {
  return <ProjectScriptWorkspace key={project} project={project} />;
}

function ProjectScriptWorkspace({ project }: { project: string }) {
  const [documents, setDocuments] = useState<ScriptDocument[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [openNodes, setOpenNodes] = useState<Set<string>>(() => new Set());
  const [setterOpen, setSetterOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [mobilePanel, setMobilePanel] = useState<"docs" | "ai" | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [instruction, setInstruction] = useState("");
  const [selection, setSelection] = useState<{ text: string; start: number; end: number } | null>(null);
  const [, redraw] = useState(0);
  const imports = useEpisodeImports(project);
  const recovery = useMemo(() => readRecovery(project), [project]);
  const hydrated = useRef(false);
  const active = useRef(false);
  const listSequence = useRef(0);
  const createMutations = useRef(new Map<string, string>());
  const manager = useMemo(() => {
    const instance = new DraftManager((id, revision, markdown, mutationId) => scriptCreationApi.save(project, id, revision, markdown, mutationId));
    return instance;
  }, [project]);

  useEffect(() => {
    active.current = true;
    const unsubscribe = manager.subscribe(() => {
      redraw((count) => count + 1);
      if (hydrated.current) writeRecovery(project, manager);
    });
    return () => {
      active.current = false;
      listSequence.current++;
      if (hydrated.current) writeRecovery(project, manager);
      unsubscribe();
      manager.dispose();
    };
  }, [manager, project]);

  const createDocument = (template: Parameters<typeof scriptCreationApi.create>[1]) => {
    const key = JSON.stringify(template);
    let mutationId = createMutations.current.get(key);
    if (!mutationId) { mutationId = crypto.randomUUID(); createMutations.current.set(key, mutationId); }
    return scriptCreationApi.create(project, template, mutationId);
  };

  const refresh = useCallback(async () => {
    const sequence = ++listSequence.current;
    const list = await scriptCreationApi.list(project);
    if (!active.current || sequence !== listSequence.current) return null;
    setDocuments(list);
    setError("");
    list.forEach((doc) => {
      manager.load(doc);
      if (!hydrated.current && recovery[doc.id]) manager.restore(doc, recovery[doc.id].markdown, recovery[doc.id].revisionId);
    });
    if (!hydrated.current) {
      hydrated.current = true;
      writeRecovery(project, manager);
    }
    setSelectedId((current) => current && list.some((doc) => doc.id === current) ? current : list[0]?.id ?? null);
    return list;
  }, [project, manager, recovery]);

  useEffect(() => {
    let alive = true;
    setLoading(true); setError("");
    void refresh().catch((cause) => { if (alive) setError(errorMessage(cause)); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [refresh]);

  const brief = documents.find((doc) => doc.kind === "brief");
  const settings = (brief && decodeBriefSettings(manager.get(brief.id)?.markdown ?? brief.revision.markdown)) ?? defaultSettings();
  const tree = treeForDocuments(documents.map((doc) => {
    const draft = manager.get(doc.id);
    return draft ? { ...draft.document, revision: { ...draft.document.revision, markdown: draft.markdown } } : doc;
  }), settings.mode);
  const current = documents.find((doc) => doc.id === selectedId);
  const draft = current && manager.get(current.id);
  const availableImports = imports.data?.data.items ?? [];
  const nextEpisodeNumber = Math.max(0, ...documents.filter((doc) => doc.kind === "episode_script").map((doc) => doc.episode_number ?? 0)) + 1;

  const saveSettings = async (value: ScriptSettings) => {
    setBusy(true); setError("");
    try {
      const latest = await refresh();
      if (!latest) return;
      const existingBrief = latest.find((doc) => doc.kind === "brief");
      if (existingBrief) {
        const currentBrief = manager.get(existingBrief.id);
        manager.edit(existingBrief.id, encodeBriefSettings(value, currentBrief?.markdown ?? existingBrief.revision.markdown));
        await manager.flush(existingBrief.id);
        if (manager.get(existingBrief.id)?.status !== "saved") throw new Error("创作设定保存失败，请重试");
      } else {
        const created = await createDocument(starterDocuments(value)[0]);
        manager.load(created);
        setDocuments((prior) => [...prior, created]);
        setSelectedId(created.id);
      }
      await refresh();
      setSetterOpen(false);
    } catch (cause) {
      setError(errorMessage(cause));
      throw cause;
    } finally { setBusy(false); }
  };

  const createBlankDocuments = async () => {
    setBusy(true); setError("");
    try {
      const latest = await refresh();
      if (!latest) return;
      const existing = new Set(latest.map((doc) => doc.kind + ":" + String(doc.episode_number ?? "")));
      const created: ScriptDocument[] = [];
      for (const template of starterDocuments(settings)) {
        const key = template.kind + ":" + String(template.episode_number ?? "");
        if (existing.has(key)) continue;
        const document = await createDocument(template);
        created.push(document);
        existing.add(key);
        manager.load(document);
        setDocuments((prior) => prior.some((entry) => entry.id === document.id) ? prior : [...prior, document]);
      }
      if (created.length) {
        setSelectedId(created.find((doc) => doc.kind !== "brief")?.id ?? created[0].id);
      }
      await refresh();
    } catch (cause) {
      await refresh().catch(() => undefined);
      setError(errorMessage(cause));
    } finally { setBusy(false); }
  };

  const createNextEpisode = async () => {
    if (settings.mode !== "series" || nextEpisodeNumber > settings.episodeCount) return;
    setBusy(true); setError("");
    try {
      const latest = await refresh();
      if (!latest) return;
      const number = Math.max(0, ...latest.filter((doc) => doc.kind === "episode_script").map((doc) => doc.episode_number ?? 0)) + 1;
      if (number > settings.episodeCount) return;
      const document = await createDocument(episodeScriptTemplate("series", number));
      manager.load(document);
      await refresh();
      setSelectedId(document.id);
      setMobilePanel(null);
    } catch (cause) {
      await refresh().catch(() => undefined);
      setError(errorMessage(cause));
    } finally { setBusy(false); }
  };

  const importEpisode = async (episodeNumber: number) => {
    setBusy(true); setError("");
    try {
      const document = await scriptCreationApi.importEpisode(project, episodeNumber);
      manager.load(document);
      await refresh();
      setSelectedId(document.id);
      setImportOpen(false);
      setMobilePanel(null);
    } catch (cause) { setError(errorMessage(cause)); }
    finally { setBusy(false); }
  };

  const navigateNode = (node: TreeNode) => {
    setSelectedId(node.documentId);
    setMobilePanel(null);
    if (node.anchor) requestAnimationFrame(() => document.getElementById("heading-" + node.documentId + "-" + node.anchor)?.scrollIntoView({ block: "start", behavior: "smooth" }));
  };
  const toggleNode = (id: string) => setOpenNodes((prior) => {
    const next = new Set(prior);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const renderNode = (node: TreeNode, depth: number): React.ReactNode => <div key={node.id}>
    <div className="flex items-center" style={{ paddingLeft: depth * 13 }}>
      <button onClick={() => toggleNode(node.id)} aria-label={"切换节点 " + node.label} className="rounded p-1 text-white/40 hover:text-white">{node.children.length ? openNodes.has(node.id) ? <ChevronDown size={14} /> : <ChevronRight size={14} /> : <span className="inline-block w-3.5" />}</button>
      <button onClick={() => { navigateNode(node); if (node.children.length && !openNodes.has(node.id)) toggleNode(node.id); }} aria-current={selectedId === node.id ? "page" : undefined} className={"min-w-0 flex-1 truncate rounded px-2 py-2 text-left text-xs " + (selectedId === node.id ? "bg-[#E5FF5C]/10 text-[#E5FF5C]" : "text-white/70 hover:bg-white/5")}>{node.label}</button>
    </div>
    {openNodes.has(node.id) && node.children.map((child) => renderNode(child, depth + 1))}
  </div>;

  if (loading) return <div className="flex h-full items-center justify-center gap-2 bg-[#0D0E10] text-sm text-white/50"><Loader2 className="size-4 animate-spin" />加载创作文档…</div>;
  if (error && !documents.length && !setterOpen) return <div className="flex h-full flex-col items-center justify-center gap-3 bg-[#0D0E10] text-sm text-white/60"><AlertCircle />{error}<button onClick={() => void refresh().catch((cause) => setError(errorMessage(cause)))} className="rounded border border-white/20 px-3 py-2">重试加载</button></div>;

  return <div className="flex h-full min-h-0 flex-col overflow-hidden bg-[#0D0E10] text-[#EDF0F2]">
    <header className="flex h-[74px] shrink-0 items-center gap-2 border-b border-white/[0.07] px-3 sm:gap-3 sm:px-5">
      <div className="flex size-9 items-center justify-center rounded bg-[#E5FF5C]/10 text-[#E5FF5C]"><FileText size={18} /></div>
      <div><h1 className="whitespace-nowrap text-base font-semibold sm:text-lg">剧本创作</h1><p className="hidden text-xs text-white/45 md:block">从一个想法，到可以继续打磨的剧本</p></div>
      <div className="flex-1" />
      <button aria-label="打开文档目录" onClick={() => setMobilePanel((current) => current === "docs" ? null : "docs")} className="rounded border border-white/15 p-2 text-white/75 lg:hidden"><FileText size={16} /></button>
      <button aria-label="打开 AI 创作" onClick={() => setMobilePanel((current) => current === "ai" ? null : "ai")} className="rounded border border-white/15 p-2 text-[#E5FF5C] lg:hidden"><MessageSquare size={16} /></button>
      <button aria-label="打开剧本设定" onClick={() => setSetterOpen(true)} className="rounded border border-white/15 p-2 text-white/75 lg:hidden"><Settings2 size={16} /></button>
      <button onClick={() => setSetterOpen(true)} className="hidden items-center gap-1.5 rounded border border-white/15 px-3 py-2 text-xs hover:border-white/35 lg:inline-flex"><Settings2 size={14} />剧本设定</button>
      {brief && <button disabled={busy} onClick={() => void createBlankDocuments()} className="hidden items-center gap-1.5 rounded border border-white/15 px-3 py-2 text-xs hover:border-white/35 lg:inline-flex"><FilePlus2 size={14} />创建空白文档</button>}
      <button onClick={() => setSetterOpen(true)} className="hidden items-center gap-1.5 rounded bg-[#E5FF5C] px-3 py-2 text-xs font-semibold text-black lg:inline-flex"><Plus size={14} />{documents.length ? "完善创作文档" : "开始原创"}</button>
    </header>
    {error && <div role="alert" className="border-b border-amber-300/20 bg-amber-300/5 px-5 py-2 text-xs text-amber-200">{error}</div>}
    {mobilePanel && <button aria-label="关闭侧栏" onClick={() => setMobilePanel(null)} className="fixed inset-x-0 bottom-0 top-[74px] z-20 bg-black/70 lg:hidden" />}
    <main className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[228px_minmax(0,1fr)_292px]">
      <aside aria-label="创作文档树" className={(mobilePanel === "docs" ? "fixed bottom-0 left-0 top-[74px] z-30 flex w-[min(85vw,320px)] shadow-2xl" : "hidden") + " min-h-0 flex-col border-r border-white/[0.07] bg-[#111317] lg:static lg:flex lg:w-auto lg:shadow-none"}>
        <div className="flex items-center justify-between px-4 py-4"><span className="text-[11px] font-semibold uppercase tracking-[.18em] text-white/45">创作文档</span><div className="flex items-center gap-3"><button onClick={() => setSetterOpen(true)} aria-label="创建创作文档" className="text-white/50 hover:text-[#E5FF5C]"><FilePlus2 size={15} /></button><button aria-label="关闭文档目录" onClick={() => setMobilePanel(null)} className="text-white/50 lg:hidden"><X size={16} /></button></div></div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2">
          {!tree.length && <p className="rounded border border-dashed border-white/10 p-4 text-xs leading-6 text-white/45">确认创作设定后，文档会出现在这里。</p>}
          {tree.map((node) => renderNode(node, 0))}
          <div className="mt-3 border-t border-white/10 px-3 py-3 text-[11px] text-white/35">{settings.mode === "single" ? "单集短片" : "连续短剧 · " + settings.episodeCount + " 集"} · {settings.durationSeconds} 秒</div>
        </div>
        <div className="border-t border-white/10 p-3">{brief && <button aria-label="创建空白文档（窄屏）" disabled={busy} onClick={() => void createBlankDocuments()} className="mb-2 w-full rounded border border-white/10 px-3 py-2 text-left text-xs text-white/65 hover:text-white lg:hidden">创建空白文档</button>}{brief && settings.mode === "series" && nextEpisodeNumber <= settings.episodeCount && <button disabled={busy} onClick={() => void createNextEpisode()} className="mb-2 w-full rounded border border-white/10 px-3 py-2 text-left text-xs text-white/65 hover:text-white">新建第 {nextEpisodeNumber} 集</button>}<button onClick={() => setImportOpen((open) => !open)} className="flex w-full items-center gap-2 rounded border border-white/10 px-3 py-2 text-left text-xs text-white/65 hover:text-white"><Import size={14} />导入已有剧本</button>
          {importOpen && <div className="mt-2 max-h-40 overflow-y-auto text-xs">{availableImports.length ? availableImports.map((item) => <button key={item.episode_number} disabled={busy} onClick={() => void importEpisode(item.episode_number)} className="block w-full rounded px-2 py-2 text-left hover:bg-white/5">第 {item.episode_number} 集 · {item.title || item.filename}</button>) : <p className="p-2 text-white/45">还没有已导入的分集。<Link to="/projects/$project/ingest" params={{ project }} className="text-[#E5FF5C]">前往剧本导入</Link></p>}</div>}
        </div>
      </aside>
      <section aria-label="文档正文" className="min-h-0 overflow-y-auto bg-[#15171B] px-4">
        {draft ? <DocumentEditor key={draft.document.id} project={project} draft={draft} manager={manager} onSelection={setSelection} /> : <div className="mx-auto flex h-full max-w-md flex-col items-center justify-center text-center">
          <div className="mb-5 flex size-16 items-center justify-center rounded-2xl border border-[#E5FF5C]/20 bg-[#E5FF5C]/5 text-[#E5FF5C]"><FileText size={30} /></div>
          <h2 className="text-xl font-semibold">你的下一部故事，从这里开始。</h2><p className="mt-3 text-sm leading-7 text-white/45">从创作预设找到方向，或写下一个想法。设定、大纲和正文都会保存在这里。</p>
          <button onClick={() => setSetterOpen(true)} className="mt-6 rounded bg-[#E5FF5C] px-5 py-2.5 text-sm font-semibold text-black">从创作预设开始</button>
          <button onClick={() => setImportOpen(true)} className="mt-3 text-xs text-white/50 hover:text-white">导入已有剧本继续打磨</button>
        </div>}
      </section>
      <aside aria-label="AI 协作" className={(mobilePanel === "ai" ? "fixed bottom-0 right-0 top-[74px] z-30 flex w-[min(90vw,360px)] shadow-2xl" : "hidden") + " min-h-0 flex-col border-l border-white/[0.07] bg-[#111317] lg:static lg:flex lg:w-auto lg:shadow-none"}>
        <div className="flex items-center gap-2 border-b border-white/10 px-4 py-4 text-xs font-semibold"><MessageSquare size={15} className="text-[#E5FF5C]" />AI 协作<button aria-label="关闭 AI 创作" onClick={() => setMobilePanel(null)} className="ml-auto text-white/50 lg:hidden"><X size={16} /></button></div>
        <GenerationPanel project={project} documents={documents} manager={manager} settings={settings}
          selected={current} instruction={instruction} onSelect={setSelectedId} onRefresh={refresh} />
        {selection && <div className="mx-4 mb-2 rounded border-l-2 border-[#E5FF5C] bg-black/20 p-2 text-xs leading-5 text-white/65">当前选段：“{selection.text}”<span className="block text-white/35">字符 {selection.start}–{selection.end}</span></div>}
        <div className="border-t border-white/10 p-4"><textarea aria-label="创作要求" value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="写下创作要求…" rows={3} className="w-full resize-none rounded border border-white/10 bg-[#0D0E10] p-3 text-xs leading-5 text-white outline-none" /></div>
      </aside>
    </main>
    {setterOpen && <ScriptSetter initial={settings} onSave={saveSettings} onClose={() => setSetterOpen(false)} />}
  </div>;
}
