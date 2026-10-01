import { useId } from "react";

export type AssetCreationPreset = "normal" | "crowd" | "unique" | "infected";

const guidance: Record<AssetCreationPreset, string> = {
  normal: "",
  crowd: "丧尸群演模板：制作单个代表人物的完整角色形象，不制作多人拼贴或人群合照。用少量模板复用群演；服装与体型变化通过身份变体管理，不为每个群演单独建角色。具体感染特征以原文设定与用户补充为准，不擅自添加眼睛颜色、血污或腐烂程度。",
  unique: "独立丧尸角色：用于有姓名、独立戏份或反复出现的丧尸，保持跨镜头可辨认的固定身份。具体感染特征以原文设定与用户补充为准，不擅自添加眼睛颜色、血污或腐烂程度。",
  infected: "感染 / 丧尸形态：作为原角色的独立身份变体，保留原角色可辨认的面部身份，不替换原有身份。感染特征以原文设定与用户补充为准，不擅自添加眼睛颜色、血污或腐烂程度。",
};

export function assetPresetContext(preset: AssetCreationPreset, context: string) {
  return [guidance[preset], context.trim()].filter(Boolean).join("\n\n");
}

export function AssetCreationPresetFields({ identity = false, preset, onPresetChange, context, onContextChange }: {
  identity?: boolean;
  preset: AssetCreationPreset;
  onPresetChange: (preset: AssetCreationPreset) => void;
  context: string;
  onContextChange: (context: string) => void;
}) {
  const id = useId();
  const options: { value: AssetCreationPreset; label: string }[] = identity
    ? [{ value: "normal", label: "普通身份" }, { value: "infected", label: "感染 / 丧尸形态" }]
    : [{ value: "normal", label: "普通角色" }, { value: "crowd", label: "丧尸群演模板" }, { value: "unique", label: "独立丧尸角色" }];
  return <div className="space-y-3 rounded-lg border border-white/10 bg-white/[0.025] p-3">
    <fieldset className="space-y-2">
      <legend className="text-xs font-medium text-muted-foreground">创建用途</legend>
      <div className="flex flex-wrap gap-2">
        {options.map(option => <label key={option.value} className="flex cursor-pointer items-center gap-2 rounded-md border border-white/10 px-2.5 py-2 text-xs has-[:checked]:border-primary has-[:checked]:text-primary">
          <input type="radio" name={id} value={option.value} checked={preset === option.value} onChange={() => onPresetChange(option.value)} className="accent-primary" />
          {option.label}
        </label>)}
      </div>
    </fieldset>
    <p className="text-xs leading-relaxed text-muted-foreground">{guidance[preset] || (identity ? "为同一角色新增服装、年龄或外观状态，原有身份保持不变。" : "普通角色按名称、剧情定位和描述建立资产。已有角色感染后，请在该角色下新增感染 / 丧尸身份。")}</p>
    <div className="space-y-1.5">
      <label htmlFor={`${id}-context`} className="text-xs font-medium text-muted-foreground">原文设定与外观细节</label>
      <textarea id={`${id}-context`} rows={3} value={context} onChange={event => onContextChange(event.target.value)} placeholder="填写原文明确的感染表现、面部特征、服装或体型；未说明的特征留空。" className="w-full rounded-lg border border-white/10 bg-white/[0.025] p-2.5 text-sm focus-visible:outline-primary" />
    </div>
    <p className="text-xs text-muted-foreground">切换用途保留已填内容。确认仅保存资产设定，不自动生成图片。</p>
  </div>;
}
