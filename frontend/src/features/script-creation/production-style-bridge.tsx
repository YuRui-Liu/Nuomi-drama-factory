import { useState } from "react";
import { useProject, useUpdateProject } from "@/lib/queries/projects";
import { useStyles } from "@/lib/queries/styles";

/** Creative styles are free text; production styles are configured presets.
 * Keep the distinction visible and require an explicit production selection. */
export function ProductionStyleBridge({ project, briefStyle }: { project: string; briefStyle: string[] }) {
  const projectQuery = useProject(project);
  const stylesQuery = useStyles(project);
  const update = useUpdateProject(project);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const current = projectQuery.data?.data.visual_style?.trim() || "chinese_period_drama";
  const styles = stylesQuery.data?.data ?? [];
  const matches = briefStyle.length === 1 ? styles.filter(style =>
    [style.id, style.label, style.name].some(label => label?.trim() === briefStyle[0].trim())) : [];
  const recommendation = matches.length === 1 ? matches[0] : null;
  const value = selected ?? current;
  const ready = !!projectQuery.data && !!stylesQuery.data && !projectQuery.isError && !stylesQuery.isError;
  const apply = async (styleId = value) => {
    setError(""); setSaved(false);
    try { await update.mutateAsync({ visual_style: styleId }); setSelected(null); setSaved(true); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "画风保存失败，请重试"); }
  };
  return <section className="mx-4 mt-3 space-y-2 rounded border border-white/10 p-3 text-xs" aria-label="创作与素材画风">
    <p>创作画风：{briefStyle.join("、") || "暂未指定"}</p>
    <p className="text-white/50">素材生成使用项目画风。保存创作设定会保留已确认的项目画风；如需更换，请在此应用。</p>
    {recommendation && recommendation.id !== current && <button className="rounded border border-[#E5FF5C]/40 px-2 py-1 text-[#E5FF5C]"
      disabled={!ready || update.isPending} onClick={() => void apply(recommendation.id)}>将创作画风应用到素材生成</button>}
    <label className="block">素材生成画风
      <select aria-label="素材生成画风" className="mt-1 w-full rounded border border-white/20 bg-[#0D0E10] p-2" value={value}
        disabled={!ready || update.isPending} onChange={event => { setSelected(event.target.value); setSaved(false); }}>
        {!styles.some(style => style.id === value) && <option value={value}>{value}</option>}
        {styles.map(style => <option key={style.id} value={style.id}>{style.label || style.name || style.id}</option>)}
      </select>
    </label>
    <button className="rounded border border-white/20 px-2 py-1" disabled={!ready || update.isPending || value === current}
      onClick={() => void apply()}>{update.isPending ? "应用中…" : "应用到素材生成"}</button>
    {saved && <p role="status">项目画风已更新，新生成的素材将使用此画风。</p>}
    {(projectQuery.isError || stylesQuery.isError) && <p role="alert">无法读取项目画风，请刷新后重试。</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
