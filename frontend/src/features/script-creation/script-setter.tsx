import { useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Dialog as DialogPrimitive } from "@base-ui/react/dialog";
import { ArrowRight, Clapperboard, Globe2, Palette, Search, Sparkles, Target, UserRound, X } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { addChoice, removeChoice } from "./settings";
import { catalog, genres, presets } from "./script-setter-data";
import type { ScriptSettings, SettingsCategory } from "./types";
import "./script-setter.css";

type Props = { initial: ScriptSettings; onSave: (settings: ScriptSettings) => void | Promise<void>; onClose: () => void };
const categoryKeys: SettingsCategory[] = ["audience", "roles", "era", "hooks", "style", "structure"];
const icons = { audience: Target, roles: UserRound, era: Globe2, hooks: Sparkles, style: Palette, structure: Clapperboard };
const presetKeys = ["genrePrimary", "genreSecondary", "audience", "roles", "era", "hooks"] as const;
type PresetFields = Pick<ScriptSettings, typeof presetKeys[number]>;
const presetFields = (preset: typeof presets[number]): PresetFields => ({
  genrePrimary: preset.primary, genreSecondary: preset.secondary,
  audience: [...preset.audience], roles: [...preset.roles], era: [...preset.era], hooks: [...preset.hooks],
});
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const whole = (value: number, minimum: number, maximum = Number.MAX_SAFE_INTEGER) =>
  Math.min(maximum, Math.max(minimum, Number.isFinite(value) ? Math.round(value) : minimum));
const normalize = (draft: ScriptSettings): ScriptSettings => ({
  ...draft, episodeCount: draft.mode === "single" ? 1 : whole(draft.episodeCount, 2, 100),
  durationSeconds: whole(draft.durationSeconds, 15),
});
const isConfirm = (event: KeyboardEvent<HTMLInputElement>, composing: boolean) =>
  event.key === "Enter" && !composing && !event.nativeEvent.isComposing && event.keyCode !== 229;

export function ScriptSetter({ initial, onSave, onClose }: Props) {
  const [draft, setDraft] = useState<ScriptSettings>(() => normalize(structuredClone(initial)));
  const [open, setOpen] = useState<SettingsCategory | null>(null);
  const [search, setSearch] = useState("");
  const [customGenres, setCustomGenres] = useState({ genrePrimary: "", genreSecondary: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showPresets, setShowPresets] = useState(() => typeof window === "undefined" || window.innerWidth > 760);
  const [undo, setUndo] = useState<{ before: PresetFields; applied: PresetFields } | null>(null);
  const saving = useRef(false);
  const composing = useRef(false);
  const mainRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const update = (patch: Partial<ScriptSettings>) => setDraft(value => ({ ...value, ...patch }));
  const activePreset = presets.findIndex(preset => presetKeys.every(key => same(draft[key], presetFields(preset)[key])));
  const applyPreset = (preset: typeof presets[number]) => {
    const applied = presetFields(preset);
    const before = Object.fromEntries(presetKeys.map(key => [key, structuredClone(draft[key])])) as PresetFields;
    setUndo({ before, applied });
    update(applied);
  };
  const undoPreset = () => {
    if (!undo) return;
    // A later manual change wins over undo, including edits to preset-controlled fields.
    const patch = Object.fromEntries(presetKeys.filter(key => same(draft[key], undo.applied[key])).map(key => [key, undo.before[key]]));
    update(patch);
    setUndo(null);
  };
  const addCustom = () => {
    if (!open || !search.trim() || composing.current) return;
    update({ [open]: addChoice(draft[open], search) });
    setSearch("");
    searchRef.current?.focus();
  };
  const toggle = (key: SettingsCategory, choice: string) => update({
    [key]: draft[key].includes(choice) ? removeChoice(draft[key], choice) : addChoice(draft[key], choice),
  });
  const addGenre = (key: "genrePrimary" | "genreSecondary") => {
    const value = customGenres[key].trim();
    if (!value || composing.current) return;
    update({ [key]: value });
    setCustomGenres(value => ({ ...value, [key]: "" }));
  };
  const save = async () => {
    if (saving.current) return;
    saving.current = true; setBusy(true); setError("");
    try { await onSave(normalize(structuredClone(draft))); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "保存失败，请重试"); }
    finally { saving.current = false; setBusy(false); }
  };
  const close = () => { if (!saving.current) onClose(); };
  const closeDrawer = () => { setDraft(normalize); setOpen(null); composing.current = false; };
  const genreList = (field: "genrePrimary" | "genreSecondary") => {
    const secondary = field === "genreSecondary";
    const options = [...new Set([...(secondary ? ["不融合"] : []), ...genres, draft[field]].filter(Boolean))];
    return <div className={"ns-genre-column" + (secondary ? " ns-secondary-column" : "")}>
      <div className="ns-genres" aria-label={secondary ? "融合题材" : "主题材"}>{options.map(genre => <button key={genre} type="button" aria-label={genre} aria-pressed={draft[field] === genre} onClick={() => update({ [field]: genre })}>
        <span aria-hidden="true" className="ns-genre-icon">{genre === "不融合" ? "−" : genre.slice(0, 1)}</span><span>{genre}</span>
      </button>)}</div>
      <div className="ns-custom-genre"><input aria-label={secondary ? "自定义融合题材" : "自定义主题材"} placeholder={secondary ? "自定义融合" : "自定义题材"} value={customGenres[field]} onChange={event => setCustomGenres(value => ({ ...value, [field]: event.target.value }))}
        onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
        onKeyDown={event => { if (isConfirm(event, composing.current)) { event.preventDefault(); addGenre(field); } }} />
        <button type="button" aria-label={secondary ? "添加自定义融合题材" : "添加自定义主题材"} onClick={() => addGenre(field)}>＋</button>
      </div>
    </div>;
  };
  const visibleGroups = open ? Object.entries(catalog[open].groups).map(([name, choices]) => [name, choices.filter(choice => choice.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()))] as const).filter(([, choices]) => choices.length > 0) : [];

  return <Dialog open onOpenChange={value => { if (!value) close(); }}>
    <DialogContent className="ns-modal" overlayClassName="ns-backdrop" showCloseButton={false} aria-label="剧本设定器">
      {showPresets && <aside className="ns-presets" aria-label="创作预设">
        <h2>创作预设</h2><p>选择起点，所有设定都可调整</p>
        <div className="ns-preset-list">{presets.map((preset, index) => <button type="button" key={preset.name} aria-pressed={activePreset === index} onClick={() => applyPreset(preset)} disabled={busy}>
          <span className="ns-preset-number">{index + 1}</span>{preset.name}<small>{preset.note}</small>
        </button>)}</div>
        <p className="ns-preset-note">预设是起点，所有设定都可调整。<br />切换会保留你的故事想法与篇幅。</p>
      </aside>}
      <div className="ns-main" ref={mainRef}>
        <header className="ns-header"><DialogTitle>剧本设定器</DialogTitle><span className="ns-badge">{activePreset >= 0 ? presets[activePreset].name : "自定义创作"}</span>
          <button type="button" className="ns-close" aria-label="关闭剧本设定器" disabled={busy} onClick={close}><X size={19} /></button>
        </header>
        <DialogDescription className="sr-only">选择创作预设，融合题材，精调六项设定并保存你的故事想法。</DialogDescription>
        <div className="ns-body"><fieldset disabled={busy} className="ns-fields">
          <div className="ns-genre-heading"><span>主题材</span><span>两种题材，碰撞出新的故事</span><span>融合题材</span></div>
          <div className="ns-genre-board">{genreList("genrePrimary")}<div className="ns-fusion-container">
            <div className="ns-fusion" aria-hidden="true"><div className="ns-disc"><strong>{draft.genrePrimary}</strong></div>{draft.genreSecondary !== "不融合" && <div className="ns-disc ns-disc-secondary"><strong>{draft.genreSecondary}</strong></div>}</div>
            <p className="ns-fusion-note"><b>{draft.genrePrimary}{draft.genreSecondary !== "不融合" && " × " + draft.genreSecondary}</b><small>{draft.genreSecondary === "不融合" ? "围绕单一题材展开" : "保留主故事类型，融入第二题材的表达"}</small></p>
          </div>{genreList("genreSecondary")}</div>
          <div className="ns-tiles">{categoryKeys.map(key => {
            const Icon = icons[key];
            const summary = key === "structure" ? (draft.mode === "single" ? "单集" : `${draft.episodeCount} 集`) + ` · ${draft.durationSeconds} 秒` : draft[key].join("、") || "暂未指定";
            return <button type="button" key={key} aria-label={"编辑" + catalog[key].title} aria-haspopup="dialog" aria-expanded={open === key} onClick={event => { triggerRef.current = event.currentTarget; setSearch(""); composing.current = false; setOpen(key); }}>
              <Icon size={19} aria-hidden="true" /><span className="ns-tile-caption">{catalog[key].title}</span><span className="ns-tile-value" title={summary}>{summary}</span>
            </button>;
          })}</div>
          <div className="ns-idea"><label htmlFor="ns-story-idea">故事的种子 <span> / 一句话，或一段自由的想象</span></label><textarea id="ns-story-idea" aria-label="故事想法" value={draft.idea} onChange={event => update({ idea: event.target.value })} rows={2} placeholder="你想讲述怎样的故事？" /></div>
        </fieldset></div>
        <footer className="ns-footer"><label className="ns-toggle"><input type="checkbox" checked={showPresets} onChange={event => setShowPresets(event.target.checked)} />显示创作预设</label>
          {undo && <button type="button" className="ns-undo" disabled={busy} onClick={undoPreset}>撤销预设</button>}
          {error && <span role="alert" className="ns-error">{error}</span>}
          <div className="ns-actions"><button type="button" disabled={busy} onClick={close}>取消</button><button type="button" className="ns-primary" disabled={busy} aria-label="保存创作设定" onClick={() => void save()}>{busy ? "保存中…" : "保存创作设定"}<ArrowRight size={14} /></button></div>
        </footer>
        <Dialog open={open !== null} onOpenChange={value => { if (!value) closeDrawer(); }}>
          {open && <DialogPrimitive.Portal container={mainRef.current}>
            <DialogPrimitive.Backdrop className="ns-drawer-shade" />
            <DialogPrimitive.Popup className="ns-drawer" aria-label={"编辑" + catalog[open].title} initialFocus={searchRef} finalFocus={triggerRef}>
              <header className="ns-drawer-header"><DialogTitle><span className="sr-only">编辑</span>{catalog[open].title}</DialogTitle><button type="button" aria-label={"关闭" + catalog[open].title} onClick={closeDrawer}><X size={18} /></button></header>
              <DialogDescription className="ns-drawer-hint">可以多选，也可以写下自己的设定，让故事保留更多可能。</DialogDescription>
              {open === "structure" && <div className="ns-structure">
                <div className="ns-mode"><button type="button" aria-pressed={draft.mode === "series"} onClick={() => update({ mode: "series", episodeCount: whole(draft.episodeCount, 2, 100) })}>连续短剧</button><button type="button" aria-pressed={draft.mode === "single"} onClick={() => update({ mode: "single", episodeCount: 1 })}>单集 / 短片</button></div>
                {draft.mode === "series" && <label>计划集数<input aria-label="计划集数" type="number" min={2} max={100} step={1} value={draft.episodeCount || ""} onChange={event => update({ episodeCount: Number(event.target.value) })} onBlur={() => update({ episodeCount: whole(draft.episodeCount, 2, 100) })} /></label>}
                <label>目标时长 / 秒<input aria-label="目标时长 / 秒" type="number" min={15} step={1} value={draft.durationSeconds || ""} onChange={event => update({ durationSeconds: Number(event.target.value) })} onBlur={() => update({ durationSeconds: whole(draft.durationSeconds, 15) })} /></label>
              </div>}
              <label className="ns-search"><Search size={16} aria-hidden="true" /><input ref={searchRef} type="search" aria-label={"搜索或自定义" + catalog[open].title} value={search} placeholder="搜索选项，或直接输入自己的设定" onChange={event => setSearch(event.target.value)}
                onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
                onKeyDown={event => { if (isConfirm(event, composing.current)) { event.preventDefault(); addCustom(); } }} /></label>
              {search.trim() && <button type="button" className="ns-add-custom" aria-label={"添加自定义" + catalog[open].title} onClick={addCustom}>＋ 使用「{search.trim()}」</button>}
              <div className="ns-options">{visibleGroups.length ? visibleGroups.map(([group, choices]) => <section key={group}><h3>{group}</h3><div className="ns-chips">{choices.map(choice => <button type="button" key={choice} aria-pressed={draft[open].includes(choice)} onClick={() => toggle(open, choice)}>{choice}</button>)}</div></section>) : <p className="ns-empty">没有匹配项，可以使用上方输入添加自己的设定。</p>}</div>
              <div className="ns-picked"><p>已选 {draft[open].length} 项 · 点击可移除</p><div className="ns-chips">{draft[open].map(choice => <button type="button" key={choice} aria-label={"移除" + choice} onClick={() => update({ [open]: removeChoice(draft[open], choice) })}>{choice}<X size={11} /></button>)}</div>{!draft[open].length && <small>暂不指定，留给创作更多可能。</small>}</div>
              <button type="button" className="ns-primary ns-done" onClick={closeDrawer}>完成设置</button>
            </DialogPrimitive.Popup>
          </DialogPrimitive.Portal>}
        </Dialog>
      </div>
    </DialogContent>
  </Dialog>;
}
