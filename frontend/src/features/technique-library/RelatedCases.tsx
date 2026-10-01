import { useTranslation } from 'react-i18next';

export function RelatedCases({ ids, onOpen }: { ids?: string[]; onOpen?: (id: string) => void }) {
  const { t } = useTranslation();
  if (!ids?.length || !onOpen) return null;
  return <div className="mt-5 border-t border-white/10 pt-4"><h4 className="mb-2 text-xs text-slate-400">{t('techniqueLibrary.relatedCases', { defaultValue: '关联案例（未本地实测）' })}</h4><div className="flex flex-wrap gap-2">{ids.map((id, index) => <button key={id} className="rounded border border-cyan-300/20 px-2 py-1 text-xs text-cyan-300" onClick={() => onOpen(id)}>{t('techniqueLibrary.case', { defaultValue: '案例' })} {index + 1}</button>)}</div></div>;
}
