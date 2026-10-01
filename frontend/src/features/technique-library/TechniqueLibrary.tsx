import { useRef, useState } from 'react';
import { BookOpen, Search, Star } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { useAuthStore } from '@/stores/auth-store';
import { directorErrorText } from '@/features/canvas/director/directorValidation';
import { browseTechniques, techniqueKey, categories, categoryLabels, categoryOf, incompatibility, type TechniqueContext } from './presentation';
import { TechniqueSketch } from './TechniqueSketch';
import { TechniqueDetail } from './TechniqueDetail';
import { useTechniqueLibrary } from './useTechniqueLibrary';

export function TechniqueLibrary({ context, label, className }: { context?: TechniqueContext; label?: string; className?: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const username = useAuthStore((state) => state.username);
  return <span className="inline-flex" onClick={(event) => event.stopPropagation()} onPointerDown={(event) => event.stopPropagation()}>
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger ref={trigger} className={className ?? 'inline-flex items-center gap-2 rounded-lg px-3 py-2 text-xs text-slate-300 hover:bg-white/5 hover:text-white'}>
        <BookOpen size={15} />{label ?? t('techniqueLibrary.title', { defaultValue: '手法库' })}
      </DialogTrigger>
      <DialogContent data-technique-library-modal="true" initialFocus={search} finalFocus={trigger}
        overlayClassName="z-[10000] bg-black/65" className="z-[10001] flex h-[min(820px,90dvh)] w-[1200px] max-w-[calc(100%-1.5rem)] flex-col gap-0 overflow-hidden border border-white/10 bg-[#13171e] p-0 text-slate-200 shadow-2xl sm:max-w-[min(1200px,calc(100%-2rem))]">
        <header className="shrink-0 border-b border-white/10 px-5 py-5 pr-12">
          <DialogTitle className="flex items-center gap-2 text-lg"><BookOpen size={19} className="text-cyan-300" />{t('techniqueLibrary.title', { defaultValue: '手法库' })}</DialogTitle>
          <DialogDescription className="mt-2 text-xs text-slate-400">{t(context ? 'techniqueLibrary.selectDescription' : 'techniqueLibrary.browseDescription', { defaultValue: context ? '为当前分段选择创作方向，保留你的故事与人物。' : '从表演到运镜，找到下一场戏的表达方式。收藏可跨项目使用。' })}</DialogDescription>
        </header>
        {open && <LibraryBody key={username ?? '__local__'} username={username} context={context} searchRef={search} onClose={() => setOpen(false)} />}
      </DialogContent>
    </Dialog>
  </span>;
}

function LibraryBody({ username, context, searchRef, onClose }: {
  username: string | null; context?: TechniqueContext;
  searchRef: React.RefObject<HTMLInputElement | null>; onClose: () => void;
}) {
  const { t } = useTranslation();
  const tr = (key: string, fallback: string) => t(`techniqueLibrary.${key}`, { defaultValue: fallback });
  const { catalog, favorites, busy, mutation } = useTechniqueLibrary(username);
  const [category, setCategory] = useState('all');
  const [query, setQuery] = useState('');
  const [showAll, setShowAll] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mobileDetail, setMobileDetail] = useState(false);
  const cards = browseTechniques(catalog.data?.techniques ?? []);
  const favoriteIds = new Set(favorites.data?.ids ?? []);
  const visible = cards.filter((card) => {
    const group = categoryOf(card);
    return (category === 'all' || (category === 'favorites' ? favoriteIds.has(card.id) : group === category))
      && (!context || showAll || !incompatibility(card, context))
      && `${card.title} ${card.summary} ${card.intent} ${card.category} ${tr(`category_${group}`, categoryLabels[group])}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
  });
  const selected = cards.find((card) => techniqueKey(card) === selectedId);
  const reset = () => { setQuery(''); setCategory('all'); setShowAll(false); };
  const issueText = (issue: string) => issue === 'capabilitiesUnavailable' ? tr(issue, '模型能力暂不可用，无法选择')
    : issue === 'modeUnavailable' ? tr(issue, '当前模型不支持此输入模式') : directorErrorText(issue, t);
  return <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
    <nav aria-label={tr('categories', '手法分类')} className={`${mobileDetail ? 'hidden lg:flex' : 'flex'} shrink-0 gap-1 overflow-x-auto border-b border-white/10 p-3 lg:w-[160px] lg:flex-col lg:border-b-0 lg:border-r lg:p-4`}>
      {(['all', 'favorites', ...categories] as const).map((value) => <button type="button" key={value} aria-pressed={category === value}
        onClick={() => { setCategory(value); setMobileDetail(false); }} className={`whitespace-nowrap rounded-lg px-3 py-2.5 text-left text-xs transition-colors ${category === value ? 'bg-cyan-300/10 text-cyan-200' : 'text-slate-400 hover:bg-white/5 hover:text-slate-200'}`}>
        {value === 'all' ? tr('all', '全部手法') : value === 'favorites' ? `☆ ${tr('favorites', '我的收藏')}` : tr(`category_${value}`, categoryLabels[value])}
      </button>)}
      <p className="mt-auto hidden px-3 pt-8 text-[10px] leading-relaxed text-slate-500 lg:block">{tr('curated', '精选创作手法\n让每个镜头都有意图')}</p>
    </nav>
    <div className={`${mobileDetail ? 'hidden lg:flex' : 'flex'} min-h-0 min-w-0 flex-1 flex-col`}>
      <div className="shrink-0 space-y-3 border-b border-white/10 p-4">
        <label className="flex items-center gap-2 rounded-lg border border-white/10 bg-black/20 px-3 text-slate-400 focus-within:border-cyan-300/60"><Search size={15} /><input ref={searchRef} type="search" aria-label={tr('search', '搜索手法')} placeholder={tr('searchPlaceholder', '搜索情绪、动作或镜头…')} value={query} onChange={(event) => setQuery(event.target.value)} className="h-10 min-w-0 flex-1 bg-transparent text-sm text-slate-200 outline-none" /></label>
        <div className="flex items-center justify-between gap-2 text-xs text-slate-400"><span>{visible.length} {tr('count', '个手法')}</span>{context && <label className="flex cursor-pointer items-center gap-2"><input type="checkbox" checked={showAll} onChange={(event) => setShowAll(event.target.checked)} className="accent-cyan-300" />{tr('showAll', '显示全部手法')}</label>}</div>
        {favorites.isError && <div role="alert" className="flex items-center justify-between gap-2 text-xs text-amber-200"><span>{tr('favoritesError', '收藏读取失败')}</span><button type="button" onClick={() => void favorites.refetch()} className="underline">{tr('retryFavorites', '重试读取收藏')}</button></div>}
        {mutation.isError && <div role="alert" className="flex items-center justify-between gap-2 text-xs text-amber-200"><span>{tr('notSaved', '未保存')}</span><button type="button" disabled={busy} onClick={() => mutation.variables && mutation.mutate(mutation.variables)} className="underline">{tr('retrySave', '重试保存')}</button></div>}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {catalog.isPending ? <p role="status" className="p-5 text-sm text-slate-400">{tr('loading', '正在加载手法…')}</p>
          : catalog.isError ? <div role="alert" className="p-5 text-sm text-amber-200">{tr('catalogError', '手法目录读取失败')} <button type="button" onClick={() => void catalog.refetch()} className="underline">{tr('retry', '重试')}</button></div>
          : category === 'favorites' && (favorites.isPending || favorites.isError) ? <p className="p-5 text-sm text-slate-400">{tr('favoritesUnknown', '收藏暂不可用，仍可浏览全部手法。')}</p>
          : visible.length === 0 ? <div className="flex h-full min-h-40 flex-col items-center justify-center gap-3 text-sm text-slate-400"><Search size={26} className="text-slate-600" /><p>{tr('empty', '没有匹配的手法')}</p><button type="button" onClick={reset} className="text-cyan-300">{tr('reset', '重置筛选')}</button></div>
          : <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">{visible.map((card) => {
            const issue = incompatibility(card, context);
            const saved = favoriteIds.has(card.id);
            return <article key={`${card.id}@${card.version}`} className={`relative overflow-hidden rounded-xl border transition-colors ${selected?.id === card.id ? 'border-cyan-300/60 bg-cyan-300/5' : 'border-white/10 bg-white/[0.02] hover:border-white/25'}`}>
              <button type="button" aria-label={`${tr('view', '查看')} ${card.title}`} onClick={() => { setSelectedId(techniqueKey(card)); setMobileDetail(true); }} className="block w-full p-3 text-left outline-offset-[-3px] focus-visible:outline-2 focus-visible:outline-cyan-300">
                <TechniqueSketch id={card.id} />
                <div className="mt-3 flex flex-wrap items-center gap-2 text-[10px] text-slate-500"><span>{tr(`category_${categoryOf(card)}`, categoryLabels[categoryOf(card)])}</span>{card.status === 'retired' && <span className="rounded bg-amber-300/10 px-1.5 py-0.5 text-amber-200">{tr('retired', '已停用')}</span>}</div>
                <h3 className="mb-1 mt-3 pr-8 text-sm font-medium text-slate-100">{card.title}</h3>
                <p className="min-h-10 text-xs leading-5 text-slate-400">{card.summary}</p>
                {context && <p className={`mt-2 text-[11px] leading-4 ${issue ? 'text-amber-200/80' : 'text-cyan-300/80'}`}>{issue ? issueText(issue) : tr('available', '当前分段可用')}</p>}
              </button>
              <button type="button" aria-label={`${saved ? tr('unfavorite', '取消收藏') : tr('favorite', '收藏')} ${card.title}`} aria-pressed={saved} disabled={busy || !favorites.isSuccess || favorites.isError}
                onClick={() => mutation.mutate({ id: card.id, favorite: !saved })} className="absolute right-4 top-4 rounded-md bg-slate-950/80 p-1.5 text-amber-200 hover:bg-slate-800 disabled:opacity-30"><Star size={15} fill={saved ? 'currentColor' : 'none'} /></button>
            </article>;
          })}</div>}
      </div>
    </div>
    <aside className={`${mobileDetail ? 'flex' : 'hidden lg:flex'} min-h-0 min-w-0 flex-1 flex-col border-white/10 bg-black/10 lg:w-[330px] lg:flex-none lg:border-l`}>
      {selected ? <TechniqueDetail card={selected} context={context} catalogAvailable={!!catalog.data && !catalog.isError} onBack={() => setMobileDetail(false)} onApply={() => {
        if (context && catalog.data && !catalog.isError && !incompatibility(selected, context)) { context.onSelect({ id: selected.id, version: selected.version }); onClose(); }
      }} /> : <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center text-sm leading-relaxed text-slate-500"><BookOpen size={30} /><p>{tr('selectDetail', '选择一张卡片，查看动作节拍与镜头细节')}</p></div>}
    </aside>
  </div>;
}
