import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { Character, CharacterVoiceDesign } from "@/types/character";

const fields = [
  ["age_impression", "听感年龄与声音表现"], ["pitch", "音区"], ["texture", "音色质感"],
  ["resonance", "共鸣"], ["articulation", "吐字"], ["pace", "习惯节奏"],
  ["accent", "口音"], ["emotional_baseline", "性格底色"],
] as const;
const performances = [["intention", "表达意图"], ["emotion", "本句情绪"], ["emphasis", "重音"], ["pauses", "停顿"], ["delivery_scale", "影视表演尺度"]] as const;

export function voiceDesignPreview(design: CharacterVoiceDesign): string {
  return [fields.map(([key]) => design.voice_spec[key]).filter(Boolean).join("，"),
    performances.map(([key]) => design.performance[key]).filter(Boolean).join("，")].filter(Boolean).join("。") + "。";
}

export function CharacterVoiceDesignForm({ character, design, context, onChange, onRefine, busy }: {
  character: Character; design: CharacterVoiceDesign; context?: Record<string, unknown>;
  onChange: (design: CharacterVoiceDesign) => void; onRefine: (instruction: string) => void; busy: boolean;
}) {
  return <div className="grid gap-5 @3xl:grid-cols-[minmax(200px,0.7fr)_minmax(0,1.5fr)]">
    <aside className="space-y-3 rounded-lg bg-muted/30 p-4 text-sm">
      <h4 className="font-medium">人物与作品依据</h4>
      <p>{character.name} · {character.role || "身份未填写"} · {character.age_group || "年龄未填写"}</p>
      <details><summary className="cursor-pointer">人物设定</summary><p className="mt-2 whitespace-pre-wrap text-xs leading-6 text-muted-foreground">{character.description || "可根据已有信息设计，缺失项作为创作建议。"}</p></details>
      <p>影视风格：{String(context?.visual_style || "沿用项目设定")}</p>
      <p className="text-xs leading-6 text-muted-foreground">{design.rationale || "将身份、性格和作品风格转成可听见的特征；下方是可编辑的创作方案。"}</p>
      <p className="text-xs text-muted-foreground">声音方案不修改人物原文事实；采用后保留音频供后续制作复用。</p>
    </aside>
    <div className="space-y-4">
      <h4 className="text-sm font-medium">稳定声线</h4>
      <div className="grid gap-3 @md:grid-cols-2">{fields.map(([key, label]) => <label key={key} className="space-y-1 text-xs">{label}<Input aria-label={label} value={design.voice_spec[key] || ""} onChange={e => onChange({ ...design, voice_spec: { ...design.voice_spec, [key]: e.target.value } })} /></label>)}</div>
      <div className="flex flex-wrap gap-2">{["听感更年轻，保留其他特征", "减少沙哑，保留其他特征", "表演更克制，保留稳定声线"].map(text => <Button key={text} size="sm" variant="outline" disabled={busy} onClick={() => onRefine(text)}>{text.split("，")[0]}</Button>)}</div>
      <h4 className="text-sm font-medium">本次试听表演</h4>
      <div className="flex flex-wrap gap-2">{["日常交流", "冲突对峙", "低声表达"].map(text => <Button key={text} size="sm" variant="ghost" disabled={busy} onClick={() => onRefine(`保留稳定声线，准备${text}的短试听例句和表演要求`)}>{text}</Button>)}</div>
      <div className="grid gap-3 @md:grid-cols-2">{performances.map(([key, label]) => <label key={key} className="space-y-1 text-xs">{label}<Input aria-label={label} value={design.performance[key] || ""} onChange={e => onChange({ ...design, performance: { ...design.performance, [key]: e.target.value } })} /></label>)}</div>
      <p className="text-xs text-muted-foreground">调整只更新文字方案，不生成音频。本句表演用于这次试听；后续配音控制取决于所用引擎。</p>
    </div>
  </div>;
}
