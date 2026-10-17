import { useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Dialog as DialogPrimitive } from "@base-ui/react/dialog";
import { Clapperboard, Globe2, Palette, Search, Sparkles, Target, UserRound, X } from "lucide-react";
import { Dialog, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { addChoice, removeChoice } from "./settings";
import { catalog, genres, presets } from "./script-setter-data";
import type { ScriptSettings, SettingsCategory } from "./types";
import "./script-setter.css";

export interface ScriptSettingsContentProps {
  value: ScriptSettings;
  onChange: (value: ScriptSettings) => void;
  disabled?: boolean;
}

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
const normalize = (settings: ScriptSettings): ScriptSettings => ({
  ...settings,
  episodeCount: settings.mode === "single" ? 1 : whole(settings.episodeCount, 2, 100),
  durationSeconds: whole(settings.durationSeconds, 15),
});
const isConfirm = (event: KeyboardEvent<HTMLInputElement>, composing: boolean) =>
  event.key === "Enter" && !composing && !event.nativeEvent.isComposing && event.keyCode !== 229;

interface DrawerDraft { category: SettingsCategory; value: ScriptSettings }

export function ScriptSettingsContent({ value, onChange, disabled = false }: ScriptSettingsContentProps) {
  const [drawer, setDrawer] = useState<DrawerDraft | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [customGenres, setCustomGenres] = useState({ genrePrimary: "", genreSecondary: "" });
  const [showPresets, setShowPresets] = useState(() => typeof window === "undefined" || window.innerWidth > 760);
  const [undo, setUndo] = useState<{ before: PresetFields; applied: PresetFields } | null>(null);
  const composing = useRef(false);
  const mainRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const update = (patch: Partial<ScriptSettings>) => onChange({ ...value, ...patch });
  const activePreset = presets.findIndex(preset => presetKeys.every(key => same(value[key], presetFields(preset)[key])));
  const applyPreset = (preset: typeof presets[number]) => {
    const applied = presetFields(preset);
    const before = Object.fromEntries(presetKeys.map(key => [key, structuredClone(value[key])])) as PresetFields;
    setUndo({ before, applied });
    update(applied);
  };
  const undoPreset = () => {
    if (!undo) return;
    // Manual changes made after applying a preset take precedence over undo.
    const patch = Object.fromEntries(presetKeys.filter(key => same(value[key], undo.applied[key])).map(key => [key, undo.before[key]]));
    update(patch);
    setUndo(null);
  };
  const openDrawer = (category: SettingsCategory, trigger: HTMLButtonElement) => {
    triggerRef.current = trigger;
    setSearch("");
    composing.current = false;
    setDrawer({ category, value: structuredClone(value) });
    setDrawerOpen(true);
  };
  const closeDrawer = (apply = false) => {
    if (apply && drawer) {
      const patch: Partial<ScriptSettings> = drawer.category === "structure"
        ? { structure: structuredClone(drawer.value.structure), mode: drawer.value.mode, episodeCount: drawer.value.episodeCount, durationSeconds: drawer.value.durationSeconds }
        : { [drawer.category]: structuredClone(drawer.value[drawer.category]) };
      onChange(normalize({ ...value, ...patch }));
    }
    setDrawerOpen(false);
    setSearch("");
    composing.current = false;
  };
  const updateDrawer = (patch: Partial<ScriptSettings>) => setDrawer(current => current ? {
    ...current, value: { ...current.value, ...patch },
  } : current);
  const addCustom = () => {
    if (!drawer || !search.trim() || composing.current) return;
    updateDrawer({ [drawer.category]: addChoice(drawer.value[drawer.category], search) });
    setSearch("");
    searchRef.current?.focus();
  };
  const toggle = (key: SettingsCategory, choice: string) => updateDrawer({
    [key]: drawer?.value[key].includes(choice) ? removeChoice(drawer.value[key], choice) : addChoice(drawer?.value[key] ?? [], choice),
  });
  const addGenre = (key: "genrePrimary" | "genreSecondary") => {
    const value = customGenres[key].trim();
    if (!value || composing.current) return;
    update({ [key]: value });
    setCustomGenres(current => ({ ...current, [key]: "" }));
  };
  const genreList = (field: "genrePrimary" | "genreSecondary") => {
    const secondary = field === "genreSecondary";
    const options = [...new Set([...(secondary ? ["不融合"] : []), ...genres, value[field]].filter(Boolean))];
    return <div className={"ns-genre-column" + (secondary ? " ns-secondary-column" : "")}>
      <div className="ns-genres" aria-label={secondary ? "融合题材" : "主题材"}>{options.map(genre => <button key={genre} type="button" aria-label={genre} aria-pressed={value[field] === genre} onClick={() => update({ [field]: genre })} disabled={disabled}>
        <span aria-hidden="true" className="ns-genre-icon">{genre === "不融合" ? "−" : genre.slice(0, 1)}</span><span>{genre}</span>
      </button>)}</div>
      <div className="ns-custom-genre"><input aria-label={secondary ? "自定义融合题材" : "自定义主题材"} placeholder={secondary ? "自定义融合" : "自定义题材"} value={customGenres[field]} disabled={disabled} onChange={event => setCustomGenres(current => ({ ...current, [field]: event.target.value }))}
        onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
        onKeyDown={event => { if (isConfirm(event, composing.current)) { event.preventDefault(); addGenre(field); } }} />
        <button type="button" aria-label={secondary ? "添加自定义融合题材" : "添加自定义主题材"} onClick={() => addGenre(field)} disabled={disabled}>＋</button>
      </div>
    </div>;
  };
  const visibleGroups = drawer ? Object.entries(catalog[drawer.category].groups)
    .map(([name, choices]) => [name, choices.filter(choice => choice.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()))] as const)
    .filter(([, choices]) => choices.length > 0) : [];

  return <div className="ns-content-layout">
    {showPresets && <aside className="ns-presets" aria-label="创作预设">
      <h2>创作预设</h2><p>选择起点，所有设定都可调整</p>
      <div className="ns-preset-list">{presets.map((preset, index) => <button type="button" key={preset.name} aria-pressed={activePreset === index} onClick={() => applyPreset(preset)} disabled={disabled}>
        <span className="ns-preset-number">{index + 1}</span>{preset.name}<small>{preset.note}</small>
      </button>)}</div>
      <p className="ns-preset-note">预设是起点，所有设定都可调整。<br />切换会保留你的故事想法与篇幅。</p>
    </aside>}
    <div className="ns-main" ref={mainRef}>
      <div className="ns-body"><fieldset disabled={disabled} className="ns-fields">
        <div className="ns-genre-heading"><span>主题材</span><span>两种题材，碰撞出新的故事</span><span>融合题材</span></div>
        <div className="ns-genre-board">{genreList("genrePrimary")}<div className="ns-fusion-container">
          <div className="ns-fusion" aria-hidden="true"><div className="ns-disc"><strong>{value.genrePrimary}</strong></div>{value.genreSecondary !== "不融合" && <div className="ns-disc ns-disc-secondary"><strong>{value.genreSecondary}</strong></div>}</div>
          <p className="ns-fusion-note"><b>{value.genrePrimary}{value.genreSecondary !== "不融合" && " × " + value.genreSecondary}</b><small>{value.genreSecondary === "不融合" ? "围绕单一题材展开" : "保留主故事类型，融入第二题材的表达"}</small></p>
        </div>{genreList("genreSecondary")}</div>
        <div className="ns-idea"><label htmlFor="ns-story-idea">故事的种子 <span> / 一句话，或一段自由的想象</span></label><textarea id="ns-story-idea" aria-label="故事想法" value={value.idea} onChange={event => update({ idea: event.target.value })} rows={2} placeholder="你想讲述怎样的故事？" /></div>
        <div className="ns-tiles">{categoryKeys.map(key => {
          const Icon = icons[key];
          const summary = key === "structure" ? (value.mode === "single" ? "单集" : `${value.episodeCount} 集`) + ` · ${value.durationSeconds} 秒` : value[key].join("、") || "暂未指定";
          return <button type="button" key={key} aria-label={"编辑" + catalog[key].title} aria-haspopup="dialog" aria-expanded={drawerOpen && drawer?.category === key} onClick={event => openDrawer(key, event.currentTarget)} disabled={disabled}>
            <Icon size={19} aria-hidden="true" /><span className="ns-tile-caption">{catalog[key].title}</span><span className="ns-tile-value" title={summary}>{summary}</span>
          </button>;
        })}</div>
      </fieldset></div>
      <footer className="ns-content-tools"><label className="ns-toggle"><input type="checkbox" checked={showPresets} onChange={event => setShowPresets(event.target.checked)} disabled={disabled} />显示创作预设</label>
        {undo && <button type="button" className="ns-undo" disabled={disabled} onClick={undoPreset}>撤销预设</button>}
        <span className="ns-badge">{activePreset >= 0 ? presets[activePreset].name : "自定义创作"}</span>
      </footer>
      <Dialog
        open={drawerOpen}
        onOpenChange={open => { if (!open) closeDrawer(); }}
        onOpenChangeComplete={open => {
          if (!open) {
            setDrawer(null);
            triggerRef.current?.focus({ preventScroll: true });
          }
        }}
      >
        {drawer && <DialogPrimitive.Portal container={mainRef.current}>
          <DialogPrimitive.Backdrop className="ns-drawer-shade" />
          <DialogPrimitive.Popup className="ns-drawer" aria-label={"编辑" + catalog[drawer.category].title} initialFocus={searchRef} finalFocus={() => triggerRef.current}>
            <header className="ns-drawer-header"><DialogTitle><span className="sr-only">编辑</span>{catalog[drawer.category].title}</DialogTitle><button type="button" aria-label={"关闭" + catalog[drawer.category].title} onClick={() => closeDrawer()} disabled={disabled}><X size={18} /></button></header>
            <DialogDescription className="ns-drawer-hint">可以多选，也可以写下自己的设定，让故事保留更多可能。</DialogDescription>
            {drawer.category === "structure" && <div className="ns-structure">
              <div className="ns-mode"><button type="button" aria-pressed={drawer.value.mode === "series"} onClick={() => updateDrawer({ mode: "series", episodeCount: whole(drawer.value.episodeCount, 2, 100) })} disabled={disabled}>连续短剧</button><button type="button" aria-pressed={drawer.value.mode === "single"} onClick={() => updateDrawer({ mode: "single", episodeCount: 1 })} disabled={disabled}>单集 / 短片</button></div>
              {drawer.value.mode === "series" && <label>计划集数<input aria-label="计划集数" type="number" min={2} max={100} step={1} value={drawer.value.episodeCount || ""} disabled={disabled} onChange={event => updateDrawer({ episodeCount: Number(event.target.value) })} onBlur={() => updateDrawer({ episodeCount: whole(drawer.value.episodeCount, 2, 100) })} /></label>}
              <label>目标时长 / 秒<input aria-label="目标时长 / 秒" type="number" min={15} step={1} value={drawer.value.durationSeconds || ""} disabled={disabled} onChange={event => updateDrawer({ durationSeconds: Number(event.target.value) })} onBlur={() => updateDrawer({ durationSeconds: whole(drawer.value.durationSeconds, 15) })} /></label>
            </div>}
            <label className="ns-search"><Search size={16} aria-hidden="true" /><input ref={searchRef} type="search" aria-label={"搜索或自定义" + catalog[drawer.category].title} value={search} placeholder="搜索选项，或直接输入自己的设定" disabled={disabled} onChange={event => setSearch(event.target.value)}
              onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
              onKeyDown={event => { if (isConfirm(event, composing.current)) { event.preventDefault(); addCustom(); } }} /></label>
            {search.trim() && <button type="button" className="ns-add-custom" aria-label={"添加自定义" + catalog[drawer.category].title} onClick={addCustom} disabled={disabled}>＋ 使用「{search.trim()}」</button>}
            <div className="ns-options">{visibleGroups.length ? visibleGroups.map(([group, choices]) => <section key={group}><h3>{group}</h3><div className="ns-chips">{choices.map(choice => <button type="button" key={choice} aria-pressed={drawer.value[drawer.category].includes(choice)} onClick={() => toggle(drawer.category, choice)} disabled={disabled}>{choice}</button>)}</div></section>) : <p className="ns-empty">没有匹配项，可以使用上方输入添加自己的设定。</p>}</div>
            <div className="ns-picked"><p>已选 {drawer.value[drawer.category].length} 项 · 点击可移除</p><div className="ns-chips">{drawer.value[drawer.category].map(choice => <button type="button" key={choice} aria-label={"移除" + choice} onClick={() => updateDrawer({ [drawer.category]: removeChoice(drawer.value[drawer.category], choice) })} disabled={disabled}>{choice}<X size={11} /></button>)}</div>{!drawer.value[drawer.category].length && <small>暂不指定，留给创作更多可能。</small>}</div>
            <div className="ns-drawer-actions"><button type="button" onClick={() => closeDrawer()} disabled={disabled}>取消编辑</button><button type="button" className="ns-primary ns-done" onClick={() => closeDrawer(true)} disabled={disabled}>完成设置</button></div>
          </DialogPrimitive.Popup>
        </DialogPrimitive.Portal>}
      </Dialog>
    </div>
  </div>;
}
