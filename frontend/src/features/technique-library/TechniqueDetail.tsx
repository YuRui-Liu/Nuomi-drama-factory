import { useTranslation } from 'react-i18next';
import type { TechniqueCard } from '@/api/videoDirector';
import { directorErrorText } from '@/features/canvas/director/directorValidation';
import { durationLabel, incompatibility, type TechniqueContext } from './presentation';
import { TechniqueSketch } from './TechniqueSketch';

export function TechniqueDetail({ card, context, catalogAvailable, onApply, onBack }: {
  card: TechniqueCard; context?: TechniqueContext; catalogAvailable: boolean; onApply: () => void; onBack: () => void;
}) {
  const { t } = useTranslation();
  const tr = (key: string, fallback: string) => t(`techniqueLibrary.${key}`, { defaultValue: fallback });
  const issue = catalogAvailable ? incompatibility(card, context) : 'techniqueCatalogUnavailable';
  return <section aria-label={tr('detail', '手法详情')} className="flex h-full min-h-0 flex-col">
    <div className="min-h-0 flex-1 overflow-y-auto p-5">
      <button type="button" className="mb-4 text-sm text-cyan-300 lg:hidden" onClick={onBack}>← {tr('back', '返回列表')}</button>
      <p className="mb-2 text-[10px] tracking-[0.16em] text-cyan-300">{tr('direction', '创作方向')} / v{card.version}</p>
      <h3 className="text-xl font-semibold text-white">{card.title}</h3>
      {card.status === 'retired' && <span className="mt-2 inline-block rounded bg-amber-300/10 px-2 py-1 text-xs text-amber-200">{tr('retired', '已停用')}</span>}
      <p className="mt-3 text-sm leading-relaxed text-slate-200">{card.summary}</p>
      <p className="mb-4 mt-2 text-sm leading-relaxed text-slate-400">{card.intent}</p>
      <TechniqueSketch id={card.id} large />
      {!!card.action_beats?.length && <div className="mt-5"><h4 className="mb-3 text-xs text-slate-400">{tr('beats', '动作节拍')}</h4>
        <ol className="space-y-3">{card.action_beats.map((beat, index) => <li key={index} className="flex gap-3 text-sm leading-relaxed"><span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-cyan-400/10 text-[10px] text-cyan-300">{index + 1}</span>{beat}</li>)}</ol></div>}
      {([['performance', '人物表演', card.performance], ['camera', '镜头调度', card.camera], ['ending', '结尾构图', card.ending_composition]] as const).map(([key, label, value]) => value && <div key={key} className="mt-5"><h4 className="mb-1 text-xs text-slate-400">{tr(key, label)}</h4><p className="text-sm leading-relaxed">{value}</p></div>)}
      {!!card.avoid?.length && <div className="mt-5 rounded-lg border border-amber-400/15 bg-amber-400/5 p-3"><h4 className="mb-2 text-xs text-amber-200">{tr('avoid', '避免')}</h4><ul className="space-y-1 text-xs leading-relaxed text-slate-300">{card.avoid.map((item) => <li key={item}>· {item}</li>)}</ul></div>}
      <div className="mt-5 border-t border-white/10 pt-4 text-xs leading-relaxed text-slate-400"><h4 className="mb-2">{tr('applicability', '适用条件')}</h4>
        <p>{card.applicability.modes.map((mode) => tr(`mode_${mode}`, { i2v: '首帧生成', fl2v: '首尾帧生成', ref_only: '参考图生成' }[mode])).join(' · ')}</p>
        <p>{durationLabel(card.applicability.min_duration_seconds)}–{durationLabel(card.applicability.max_duration_seconds)} {tr('seconds', '秒')} · {tr(`frame_${card.applicability.last_frame_constraint}`, { required: '需要尾帧', allowed: '可选尾帧', forbidden: '不使用尾帧' }[card.applicability.last_frame_constraint])}</p>
      </div>
      {!!card.sources?.length && <div className="mt-4 text-xs"><h4 className="mb-2 text-slate-400">{tr('sources', '参考来源')}</h4>{card.sources.map((source) => <a key={source.url} href={source.url} target="_blank" rel="noreferrer" className="mb-2 block break-words text-cyan-300 underline-offset-4 hover:underline">{source.credit} ↗</a>)}</div>}
    </div>
    {context && <div className="border-t border-white/10 p-4">
      {issue && <p className="mb-3 text-xs text-amber-200">{issue === 'capabilitiesUnavailable' ? tr(issue, '模型能力暂不可用，无法选择') : issue === 'modeUnavailable' ? tr(issue, '当前模型不支持此输入模式') : directorErrorText(issue, t)}</p>}
      <button type="button" disabled={!!issue} className="w-full rounded-lg bg-cyan-300 px-4 py-3 text-sm font-medium text-slate-950 hover:bg-cyan-200 disabled:cursor-not-allowed disabled:opacity-35" onClick={onApply}>{tr('apply', '应用到当前分段')}</button>
    </div>}
  </section>;
}
