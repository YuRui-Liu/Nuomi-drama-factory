import { useRef, useState } from "react";
import { Check, ChevronDown, Search, X } from "lucide-react";
import { addChoice, removeChoice } from "./settings";
import type { ScriptSettings, SettingsCategory } from "./types";

const genres = ["玄幻修仙", "都市现实", "古装言情", "悬疑推理", "惊悚怪谈", "末世科幻", "历史传奇", "喜剧", "剧情", "热血竞技"];
const catalog: Record<SettingsCategory, { title: string; groups: Record<string, string[]> }> = {
  audience: { title: "目标受众", groups: {
    "观众方向": ["大众向", "男频", "女频", "年轻职场人", "女性成长", "家庭观众", "青年学生", "成熟观众"],
    "内容偏好": ["轻松解压", "情感共鸣", "爽感逆袭", "烧脑推理", "现实议题", "传统文化", "幻想冒险", "亲子陪伴"],
  }},
  roles: { title: "角色设定", groups: {
    "身份原型": ["普通打工人", "落魄天才", "宗门弟子", "世家继承人", "市井小人物", "独立创业者", "刑侦人员", "医生", "记者", "古代女官", "退役军人", "非人主角"],
    "人物关系": ["师徒", "同门", "欢喜冤家", "宿敌", "同事", "陌生搭档", "重组家庭", "代际关系", "契约关系", "隐秘亲缘"],
  }},
  era: { title: "时代背景", groups: {
    "古代 / 历史": ["上古神话", "先秦", "秦汉", "魏晋南北朝", "隋唐", "宋元", "明清", "古代架空"],
    "近现代": ["晚清变局", "民国", "20 世纪五六十年代", "改革开放初期", "20 世纪九十年代"],
    "当代 / 未来": ["当代都市", "当代乡村", "近未来", "远未来", "星际文明", "末日之后"],
    "架空 / 世界": ["仙侠世界", "武侠江湖", "平行时空", "东方奇幻", "西方奇幻", "蒸汽朋克", "赛博朋克"],
  }},
  hooks: { title: "核心看点", groups: {
    "人物与成长": ["小人物逆袭", "隐藏身份", "天才被低估", "职业成长", "女性成长", "反英雄", "师徒传承"],
    "关系与情感": ["双向救赎", "欢喜冤家", "破镜重圆", "先婚后爱", "代际和解", "信任与背叛", "宿敌合作"],
    "情节与机制": ["身份错位", "时间循环", "重生改命", "穿越生存", "规则怪谈", "层层解谜", "极限求生", "金手指", "群像博弈"],
    "表达与体验": ["反差喜剧", "职场讽刺", "温暖治愈", "现实困境", "东方美学", "热血竞技", "悬念递进"],
  }},
  style: { title: "画风", groups: {
    "项目风格": ["沿用项目已确认风格"],
    "动画": ["3D 国漫动画", "二维动画", "日系动画", "美式动画", "水墨动画", "定格动画", "像素风"],
    "写实与美术": ["真人影视", "电影写实", "写实国风", "复古胶片", "黑白电影", "绘本插画", "轻写实", "赛博视觉"],
  }},
  structure: { title: "剧本结构", groups: {
    "叙事方式": ["人物驱动", "单线叙事", "双线并行", "群像叙事", "单元故事", "时间循环", "非线性叙事"],
  }},
};

const presets: { name: string; note: string; primary: string; secondary: string; era: string[]; hooks: string[]; roles: string[]; audience: string[] }[] = [
  { name: "玄幻修仙逆袭", note: "小人物 · 身份反差", primary: "玄幻修仙", secondary: "喜剧", era: ["仙侠世界", "古代架空"], hooks: ["天才被低估", "职场讽刺"], roles: ["宗门弟子", "普通打工人"], audience: ["年轻职场人", "大众向"] },
  { name: "现实职场成长", note: "现实处境 · 人物成长", primary: "都市现实", secondary: "剧情", era: ["当代都市"], hooks: ["职业成长", "现实困境"], roles: ["普通打工人", "同事"], audience: ["年轻职场人"] },
  { name: "末世生存悬疑", note: "生存抉择 · 未知谜团", primary: "末世科幻", secondary: "悬疑推理", era: ["近未来", "末日之后"], hooks: ["极限求生", "层层解谜"], roles: ["陌生搭档"], audience: ["大众向", "烧脑推理"] },
  { name: "非遗国潮视觉", note: "手艺传承 · 东方美学", primary: "都市现实", secondary: "历史传奇", era: ["当代乡村"], hooks: ["师徒传承", "东方美学"], roles: ["师徒"], audience: ["传统文化"] },
  { name: "古代女频甜宠", note: "情感关系 · 命运相遇", primary: "古装言情", secondary: "喜剧", era: ["古代架空"], hooks: ["欢喜冤家", "双向救赎"], roles: ["古代女官", "世家继承人"], audience: ["女频", "情感共鸣"] },
  { name: "规则怪谈悬疑", note: "规则试探 · 悬念递进", primary: "惊悚怪谈", secondary: "悬疑推理", era: ["平行时空"], hooks: ["规则怪谈", "悬念递进"], roles: ["市井小人物"], audience: ["烧脑推理"] },
];

type Props = { initial: ScriptSettings; onSave: (settings: ScriptSettings) => void | Promise<void>; onClose: () => void };
const categoryKeys: SettingsCategory[] = ["audience", "roles", "era", "hooks", "style", "structure"];

export function ScriptSetter({ initial, onSave, onClose }: Props) {
  const [draft, setDraft] = useState<ScriptSettings>(() => structuredClone(initial));
  const [open, setOpen] = useState<SettingsCategory | null>(null);
  const [search, setSearch] = useState("");
  const [custom, setCustom] = useState("");
  const [customPrimary, setCustomPrimary] = useState("");
  const [customSecondary, setCustomSecondary] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showPresets, setShowPresets] = useState(true);
  const composing = useRef(false);
  const update = (patch: Partial<ScriptSettings>) => setDraft((value) => ({ ...value, ...patch }));
  const addCustom = (key: SettingsCategory) => {
    if (!custom.trim()) return;
    update({ [key]: addChoice(draft[key], custom) });
    setCustom("");
    setSearch("");
  };
  const toggle = (key: SettingsCategory, choice: string) => update({
    [key]: draft[key].includes(choice) ? removeChoice(draft[key], choice) : addChoice(draft[key], choice),
  });
  const applyPreset = (preset: typeof presets[number]) => update({
    genrePrimary: preset.primary, genreSecondary: preset.secondary, era: preset.era,
    hooks: preset.hooks, roles: preset.roles, audience: preset.audience,
  });
  const save = async () => {
    setBusy(true); setError("");
    try { await onSave(draft); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "保存失败，请重试"); }
    finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-3" role="presentation">
      <section role="dialog" aria-modal="true" aria-label="剧本设定器" className="flex max-h-[92vh] w-full max-w-[1180px] overflow-hidden rounded-xl border border-white/10 bg-[#14161A] text-[#ECEFF2] shadow-2xl">
        {showPresets && <aside aria-label="创作预设" className="hidden w-52 shrink-0 overflow-y-auto border-r border-white/10 bg-[#101216] p-4 md:block">
          <h2 className="text-base font-semibold">创作预设</h2>
          <p className="mt-1 text-xs text-white/50">选择起点，所有设定都可调整</p>
          <div className="mt-5 space-y-2">{presets.map((preset, index) =>
            <button key={preset.name} type="button" onClick={() => applyPreset(preset)} className="w-full rounded-md border border-white/10 px-3 py-2 text-left text-sm hover:border-[#E5FF5C]/50 hover:bg-[#E5FF5C]/5">
              <span className="mr-2 text-[#E5FF5C]">{String(index + 1).padStart(2, "0")}</span>{preset.name}
              <small className="block pl-7 text-white/45">{preset.note}</small>
            </button>)}</div>
          <p className="mt-5 text-xs leading-5 text-white/45">预设只是创作起点，可继续调整。</p>
        </aside>}
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex items-center gap-3 border-b border-white/10 px-6 py-4">
            <h2 className="text-lg font-semibold">剧本设定器</h2>
            <span className="rounded bg-[#E5FF5C]/10 px-2 py-1 text-xs text-[#E5FF5C]">自定义创作</span>
            <button className="ml-auto rounded p-1 hover:bg-white/10" onClick={onClose} aria-label="关闭剧本设定器"><X size={18} /></button>
          </header>
          <div className="min-h-0 space-y-5 overflow-y-auto p-6">
            <div className="grid gap-3 lg:grid-cols-[1fr_180px_1fr]">
              {(["genrePrimary", "genreSecondary"] as const).map((field, index) => <div key={field} className={index ? "lg:col-start-3" : ""}>
                <h3 className="mb-2 text-xs font-medium text-white/55">{index ? "融合题材" : "主题材"}</h3>
                <div className="grid grid-cols-2 gap-1.5">
                  {(index ? ["不融合", ...genres] : genres).map((genre) =>
                    <button key={genre} aria-pressed={draft[field] === genre} onClick={() => update({ [field]: genre })} className={"rounded border px-2 py-1.5 text-left text-xs " + (draft[field] === genre ? "border-[#E5FF5C] bg-[#E5FF5C]/10 text-[#E5FF5C]" : "border-white/10 text-white/75 hover:border-white/30")}>{genre}</button>)}
                </div>
                <div className="mt-2 flex gap-1"><input aria-label={index ? "自定义融合题材" : "自定义主题材"} value={index ? customSecondary : customPrimary} onChange={(event) => index ? setCustomSecondary(event.target.value) : setCustomPrimary(event.target.value)} className="min-w-0 flex-1 rounded border border-white/10 bg-black/20 px-2 py-1.5 text-xs" placeholder="自定义题材" />
                  <button aria-label={index ? "添加自定义融合题材" : "添加自定义主题材"} onClick={() => { const value = (index ? customSecondary : customPrimary).trim(); if (value) update({ [field]: value }); }} className="rounded border border-white/10 px-2 text-[#E5FF5C]">＋</button></div>
              </div>)}
              <div className="row-start-2 flex items-center justify-center rounded border border-white/10 bg-[#0D0E10] p-3 text-center text-xs text-[#E5FF5C] lg:col-start-2 lg:row-start-1">
                {draft.genrePrimary}<br />{draft.genreSecondary === "不融合" ? "单题材" : "× " + draft.genreSecondary}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-3">{categoryKeys.map((key) => <button key={key} aria-label={"编辑" + catalog[key].title} onClick={() => { setOpen(open === key ? null : key); setSearch(""); setCustom(""); }} className={"rounded-lg border p-3 text-left " + (open === key ? "border-[#E5FF5C]/60 bg-[#E5FF5C]/5" : "border-white/10 bg-white/[0.025]")}>
              <span className="flex items-center justify-between text-sm font-medium">{catalog[key].title}<ChevronDown size={14} /></span>
              <small className="mt-1 block truncate text-white/50">{key === "structure" ? (draft.mode === "single" ? "单集" : String(draft.episodeCount) + " 集") + " · " + String(draft.durationSeconds) + " 秒" : draft[key].join("、") || "选择或手动填写"}</small>
            </button>)}</div>
            {open && <section className="rounded-lg border border-white/10 bg-[#0D0E10] p-4">
              <h3 className="mb-3 font-medium">{catalog[open].title}</h3>
              {open === "structure" && <div className="mb-4 flex flex-wrap items-end gap-3">
                <button aria-pressed={draft.mode === "series"} onClick={() => update({ mode: "series" })} className={"rounded border px-3 py-2 text-xs " + (draft.mode === "series" ? "border-[#E5FF5C] text-[#E5FF5C]" : "border-white/20")}>连续短剧</button>
                <button aria-pressed={draft.mode === "single"} onClick={() => update({ mode: "single" })} className={"rounded border px-3 py-2 text-xs " + (draft.mode === "single" ? "border-[#E5FF5C] text-[#E5FF5C]" : "border-white/20")}>单集 / 短片</button>
                {draft.mode === "series" && <label className="text-xs">计划集数<input aria-label="计划集数" type="number" min={2} max={200} value={draft.episodeCount} onChange={(event) => update({ episodeCount: Math.max(2, Number(event.target.value) || 2) })} className="ml-2 w-20 rounded border border-white/20 bg-black/20 p-2" /></label>}
                <label className="text-xs">目标时长 / 秒<input aria-label="目标时长 / 秒" type="number" min={15} value={draft.durationSeconds} onChange={(event) => update({ durationSeconds: Math.max(15, Number(event.target.value) || 15) })} className="ml-2 w-24 rounded border border-white/20 bg-black/20 p-2" /></label>
              </div>}
              <label className="flex items-center gap-2 rounded border border-white/10 bg-white/[0.025] px-3 py-2"><Search size={14} className="text-white/40" /><input type="search" aria-label={"搜索" + catalog[open].title} value={search} onChange={(event) => { setSearch(event.target.value); setCustom(event.target.value); }} placeholder="搜索候选项，找不到也可以自己填…" className="w-full bg-transparent text-sm outline-none" /></label>
              <div className="mt-3 max-h-44 space-y-3 overflow-y-auto">{Object.entries(catalog[open].groups).map(([group, choices]) => {
                const visible = choices.filter((choice) => choice.includes(search.trim()));
                return visible.length ? <div key={group}><h4 className="mb-1 text-xs text-white/45">{group}</h4><div className="flex flex-wrap gap-1.5">{visible.map((choice) => <button key={choice} aria-pressed={draft[open].includes(choice)} onClick={() => toggle(open, choice)} className={"rounded-full border px-2.5 py-1 text-xs " + (draft[open].includes(choice) ? "border-[#E5FF5C] text-[#E5FF5C]" : "border-white/15 hover:border-white/35")}>{choice}</button>)}</div></div> : null;
              })}</div>
              <div className="mt-3 flex gap-2"><input aria-label={"自定义" + catalog[open].title} value={custom} onChange={(event) => setCustom(event.target.value)} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }} onKeyDown={(event) => {
                if (event.key !== "Enter" || composing.current || event.nativeEvent.isComposing || event.keyCode === 229) return;
                event.preventDefault();
                addCustom(open);
              }} className="min-w-0 flex-1 rounded border border-white/10 bg-white/[0.025] px-3 py-2 text-sm" placeholder="手动填写，回车添加" /><button aria-label={"添加自定义" + catalog[open].title} onClick={() => addCustom(open)} className="rounded bg-[#E5FF5C] px-3 text-xs font-semibold text-black">＋ 添加</button></div>
              <div className="mt-3 flex flex-wrap gap-1.5">{draft[open].map((choice) => <button key={choice} aria-label={"移除" + choice} onClick={() => update({ [open]: removeChoice(draft[open], choice) })} className="flex items-center gap-1 rounded-full bg-[#E5FF5C]/10 px-2.5 py-1 text-xs text-[#E5FF5C]">{choice}<X size={11} /></button>)}</div>
            </section>}
            <label className="block text-xs text-white/60">故事想法<textarea aria-label="故事想法" value={draft.idea} onChange={(event) => update({ idea: event.target.value })} rows={3} placeholder="一句话描述你的故事（可稍后补充）" className="mt-2 w-full resize-y rounded border border-white/10 bg-black/20 p-3 text-sm text-white" /></label>
          </div>
          <footer className="flex items-center gap-3 border-t border-white/10 px-6 py-4">
            <label className="mr-auto flex items-center gap-2 text-xs text-white/50"><input type="checkbox" checked={showPresets} onChange={(event) => setShowPresets(event.target.checked)} />显示创作预设</label>
            {error && <span role="alert" className="text-xs text-red-400">{error}</span>}
            <button onClick={onClose} className="rounded px-3 py-2 text-sm text-white/60">取消</button>
            <button disabled={busy} onClick={() => void save()} className="inline-flex items-center gap-1 rounded bg-[#E5FF5C] px-4 py-2 text-sm font-semibold text-black disabled:opacity-50"><Check size={15} />保存创作设定</button>
          </footer>
        </div>
      </section>
    </div>
  );
}
