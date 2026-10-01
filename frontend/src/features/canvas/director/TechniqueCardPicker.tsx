import { useTranslation } from 'react-i18next';
import type { DirectorSegment } from '../domain/canvasNodes';
import type { DirectorCapabilities, TechniqueCard } from '@/api/videoDirector';
import { directorErrorText } from './directorValidation';
import { TechniqueLibrary } from '@/features/technique-library/TechniqueLibrary';

export function TechniqueCardPicker({ segment, segmentIndex, hasReferences, capabilities, techniques, error, errorField, onSelect }: {
  segment: DirectorSegment; hasReferences: boolean; capabilities: DirectorCapabilities | null;
  techniques: TechniqueCard[] | null; segmentIndex?: number; error?: string; errorField?: string;
  onSelect: (selection: DirectorSegment['technique']) => void;
}) {
  const { t } = useTranslation();
  const tr = (key: string, defaultValue: string) => t(`node.videoDirector.techniques.${key}`, { defaultValue });
  const selected = segment.technique;
  const card = techniques?.find((item) => item.id === selected?.id && item.version === selected?.version);
  const selectedTitle = card?.title ?? (selected ? `${selected.id}@${selected.version}` : '');
  const fieldLabel = errorField ? tr(`field_${errorField}`, errorField) : '';
  return <section className="mt-3 rounded-lg border border-white/10 p-3 text-xs" aria-label={tr('label', '手法卡片')}>
    <div className="flex items-center justify-between gap-2">
      <span className="text-slate-400">{tr('label', '手法卡片')}</span>
      <TechniqueLibrary label={t(selected ? 'techniqueLibrary.replace' : 'techniqueLibrary.choose', { defaultValue: selected ? '更换手法' : '选择手法' })}
        className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-cyan-300 hover:bg-cyan-300/10"
        context={{ segment, hasReferences, capabilities, onSelect }} />
    </div>
    {selected ? <div className="mt-2 flex items-start justify-between gap-3"><div><strong>{selectedTitle}</strong><p className="mt-1 leading-relaxed text-slate-400">{card?.summary ?? tr('unavailable', '当前不可用')}</p></div><button type="button" className="shrink-0 text-slate-400 hover:text-white" onClick={() => onSelect(null)}>{tr('clear', '清除卡片')}</button></div>
      : <p className="mt-2 text-slate-500">{tr('none', '不使用卡片')}</p>}
    {!techniques && <p className="mt-2 text-amber-300">{tr('catalogUnavailable', '卡片目录暂不可用')}</p>}
    {error && <p role="alert" className="mt-2 text-red-300">
      {segmentIndex === undefined ? '' : `${t('node.videoDirector.editor.segment', { defaultValue: '分段' })} ${segmentIndex + 1} · `}
      {selectedTitle ? `${selectedTitle} · ` : ''}{fieldLabel ? `${fieldLabel}：` : ''}{directorErrorText(error, t)}
    </p>}
  </section>;
}
