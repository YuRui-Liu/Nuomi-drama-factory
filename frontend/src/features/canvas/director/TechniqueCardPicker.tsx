import { useTranslation } from 'react-i18next';
import type { DirectorSegment } from '../domain/canvasNodes';
import type { DirectorCapabilities, TechniqueCard } from '@/api/videoDirector';
import { directorErrorText, techniqueCompatibility } from './directorValidation';

export function TechniqueCardPicker({ segment, segmentIndex, hasReferences, capabilities, techniques, error, errorField, onSelect }: {
  segment: DirectorSegment; hasReferences: boolean; capabilities: DirectorCapabilities | null;
  techniques: TechniqueCard[] | null; segmentIndex?: number; error?: string; errorField?: string;
  onSelect: (selection: DirectorSegment['technique']) => void;
}) {
  const { t } = useTranslation();
  const tr = (key: string, defaultValue: string) => t(`node.videoDirector.techniques.${key}`, { defaultValue });
  const selected = segment.technique ? `${segment.technique.id}@${segment.technique.version}` : '';
  const known = techniques?.some((card) => `${card.id}@${card.version}` === selected);
  const selectedTitle = techniques?.find((card) => `${card.id}@${card.version}` === selected)?.title ?? selected;
  const fieldLabel = errorField ? tr(`field_${errorField}`, errorField) : '';
  const reason = (card: TechniqueCard) => capabilities
    ? techniqueCompatibility(card, segment, hasReferences, capabilities) : null;
  return <section className="mt-3 rounded-lg border border-white/10 p-3 text-xs" aria-label={tr('label', '手法卡片')}>
    <label className="block">{tr('label', '手法卡片')}
      <select className="mt-1 block w-full rounded bg-black/30 p-2" aria-label={tr('label', '手法卡片')}
        value={selected} onChange={(event) => {
          const card = techniques?.find((item) => `${item.id}@${item.version}` === event.target.value);
          onSelect(card ? { id: card.id, version: card.version } : null);
        }}>
        <option value="">{tr('none', '不使用卡片')}</option>
        {selected && !known && <option value={selected} disabled>{selected} · {tr('unavailable', '当前不可用')}</option>}
        {techniques?.map((card) => {
          const issue = reason(card);
          return <option key={`${card.id}@${card.version}`} value={`${card.id}@${card.version}`} disabled={!!issue}>
            {card.title} · {card.summary}{issue ? ` · ${directorErrorText(issue, t)}` : ''}
          </option>;
        })}
      </select>
    </label>
    {selected && <button type="button" className="mt-2 text-cyan-300" onClick={() => onSelect(null)}>{tr('clear', '清除卡片')}</button>}
    {!techniques && <p className="mt-2 text-amber-300">{tr('catalogUnavailable', '卡片目录暂不可用')}</p>}
    {error && <p role="alert" className="mt-2 text-red-300">
      {segmentIndex === undefined ? '' : `${t('node.videoDirector.editor.segment', { defaultValue: '分段' })} ${segmentIndex + 1} · `}
      {selectedTitle ? `${selectedTitle} · ` : ''}{fieldLabel ? `${fieldLabel}：` : ''}{directorErrorText(error, t)}
    </p>}
    {!!techniques?.length && <ul className="mt-2 space-y-2">{techniques.map((card) => {
      const issue = reason(card);
      return <li key={`${card.id}@${card.version}`} className="rounded border border-white/10 p-2">
        <strong>{card.title}</strong> · {card.summary}
        <p>{tr('limits', '适用')}：{card.applicability.modes.join(', ')} · {card.applicability.min_duration_seconds}–{card.applicability.max_duration_seconds}s · {tr(`lastFrame_${card.applicability.last_frame_constraint}`, card.applicability.last_frame_constraint)}</p>
        {card.sources?.map((source) => <a key={source.url} href={source.url} target="_blank" rel="noreferrer" className="mr-2 text-cyan-300">{source.credit}</a>)}
        {issue && <p className="text-amber-300">{directorErrorText(issue, t)}</p>}
      </li>;
    })}</ul>}
  </section>;
}
