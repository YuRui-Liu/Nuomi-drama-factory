import { useState } from "react";
import { CharacterCastingPanel } from "@/components/assets/character-casting-panel";
import { useCharacters } from "@/lib/queries/characters";
import { readStudioContext } from "./studio-context";
import { CharacterLibrary } from "./character-library";
import { CharacterCostume } from "./character-costume";
import { CharacterImpact } from "./character-impact";
import { useCharacterSwitch } from "./character-draft-bridge";
import { Button } from "@/components/ui/button";
import { resolveMediaUrl } from "@/lib/media-url";
import { Search, UserRound, Check, Shirt, ScanFace } from "lucide-react";

function characterSummary(item: { role?: string; description?: string }) {
  const text = (item.role?.trim() || item.description?.trim() || "待完善").replace(/\s+/g, " ");
  const characters = Array.from(text);
  return characters.length > 64 ? `${characters.slice(0, 64).join("")}…` : text;
}

export function CharacterStudio({ project }: { project: string }) {
  return <CharacterWorkspace key={project} project={project} />;
}

function CharacterWorkspace({ project }: { project: string }) {
  const characters = useCharacters(project);
  const [name, setName] = useState(() => readStudioContext(window.location.search, project).character ?? "");
  const [mode, setMode] = useState("identity");
  const [search, setSearch] = useState("");
  const switching = useCharacterSwitch();
  const items = characters.data?.data ?? [];
  const selected = items.find((item) => item.name === name);
  const matching = items.filter((item) => [item.name, item.role, item.description, ...(item.aliases ?? [])].some((value) => value?.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())));
  return <section className="character-workspace" aria-label="角色造型室">
    <aside className="character-rail">
    <div className="flex items-center justify-between gap-2"><h2 className="text-sm font-semibold">项目角色</h2><span className="text-xs text-muted-foreground">{items.length} 位</span></div>
    {characters.isPending && <p role="status">正在加载角色…</p>}
    {characters.error && <p role="alert">角色加载失败：{characters.error.message}</p>}
    {!characters.isPending && !characters.error && items.length === 0 && <p>项目中还没有角色，请先在角色页创建或从剧本提取角色。</p>}
    <section aria-label="项目角色" className="space-y-3">
      <label className="flex w-full items-center gap-2 rounded-lg border bg-background px-2 py-2"><Search size={15} className="shrink-0 text-muted-foreground" /><input type="search" aria-label="搜索项目角色" placeholder="搜索角色" value={search} onChange={(event) => setSearch(event.target.value)} className="min-w-0 flex-1 bg-transparent text-xs outline-none" /></label>
      <div className="character-rail-list">{matching.map((item) => <button key={item.name} type="button" aria-label={`选择角色 ${item.name}`} aria-pressed={item.name === name} onClick={() => { if (item.name !== name) switching.request(() => setName(item.name)); }} className={`group relative flex items-center gap-2 overflow-hidden rounded-lg border p-2 text-left transition focus-visible:outline-2 focus-visible:outline-primary ${item.name === name ? "border-primary bg-primary/5" : "border-border/70 bg-background/50 hover:border-primary/50"}`}>
        <div className="flex h-14 w-11 shrink-0 items-center justify-center overflow-hidden rounded bg-muted/40">{item.portrait_url ? <img loading="lazy" src={resolveMediaUrl(item.portrait_url) ?? undefined} alt={`${item.name}当前形象`} className="h-full w-full object-cover object-top" /> : <UserRound size={24} strokeWidth={1} className="text-muted-foreground" />}</div>
        {item.name === name && <span className="absolute right-2 top-2 rounded-full bg-lime-400 p-1 text-black"><Check size={12} /></span>}
        <div className="min-w-0 space-y-1"><span className="block truncate text-sm font-medium">{item.name}</span><span className="block truncate text-xs text-muted-foreground">{characterSummary(item)}</span></div>
      </button>)}</div>
      {items.length > 0 && matching.length === 0 && <p className="py-5 text-center text-sm text-muted-foreground">没有匹配的角色，请调整搜索词。</p>}
    </section>
    </aside>
    <div className="character-editor">
    {name && !selected && <p role="alert">该角色已不存在，请重新选择。</p>}
    {selected?.description && <details key={`description/${selected.name}`} className="rounded-lg border px-3 py-2"><summary className="cursor-pointer text-xs">查看 {selected.name} 完整描述</summary><p className="mt-2 whitespace-pre-wrap break-words text-sm text-muted-foreground">{selected.description}</p></details>}
    {selected && <><div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="font-semibold">{selected.name}</h3><p className="mt-1 text-xs text-muted-foreground">先确定人物身份，再探索不同服装造型。</p></div><div role="group" aria-label="编辑模式" className="inline-flex rounded-xl border bg-muted/30 p-1"><Button variant={mode === "identity" ? "secondary" : "ghost"} aria-pressed={mode === "identity"} onClick={() => { if (mode !== "identity") switching.request(() => setMode("identity")); }}><ScanFace size={15} />基础身份与面部</Button><Button variant={mode === "costume" ? "secondary" : "ghost"} aria-pressed={mode === "costume"} onClick={() => { if (mode !== "costume") switching.request(() => setMode("costume")); }}><Shirt size={15} />服装造型</Button></div></div>{mode === "identity" ? <CharacterCastingPanel studioBridge key={`${project}/${name}`} project={project} name={name} /> : <CharacterCostume key={`${project}/${name}`} project={project} name={name} />}<CharacterImpact key={`impact/${project}/${name}`} project={project} name={name} /></>}
    {!selected && !characters.isPending && <div className="flex min-h-80 flex-col items-center justify-center gap-3 rounded-xl border border-dashed text-muted-foreground"><ScanFace size={40} strokeWidth={1} /><p>从左侧选择角色，开始设计身份与服装</p></div>}
    {switching.dialog}
    <details className="shrink-0 rounded-lg border px-3 py-2"><summary className="cursor-pointer text-xs">角色卡库 · 保存快照与跨项目复用</summary><CharacterLibrary key={`library/${project}/${name}`} project={project} character={selected} /></details>
    </div>
  </section>;
}
