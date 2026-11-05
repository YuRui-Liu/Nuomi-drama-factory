import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { ChevronDown, ChevronRight, PanelLeftClose, Pencil, Plus, Search, Trash2 } from 'lucide-react';
import { useMediaQuery } from '@/hooks/use-media-query';
import type { CreationCanvas, WorkspaceKind } from './contracts';
import { CreateCanvasDialog } from './CreateCanvasDialog';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';

const groups: { kind: WorkspaceKind; label: string }[] = [
  { kind: 'concept', label: '构思画布' }, { kind: 'episode', label: '分集画布' }, { kind: 'free', label: '自由画布' },
];
const statusLabel = { applied: '', running: '创建中', conflict: '创建冲突' };

export function CanvasWorkspaceSwitcher({ canvases, selectedId, onSelect, onCreate, pending, retry, error, registryError, onRetryRegistry, onRename, onDelete }: {
  canvases: CreationCanvas[]; selectedId?: string; onSelect: (id: string) => void;
  onCreate: (kind: WorkspaceKind, title: string) => Promise<boolean>; pending: boolean; retry?: () => Promise<boolean>; error?: string;
  registryError?: string; onRetryRegistry?: () => void;
  onRename?: (canvas: CreationCanvas, title: string) => Promise<boolean>;
  onDelete?: (canvas: CreationCanvas) => Promise<boolean>;
}) {
  const [search, setSearch] = useState('');
  const [collapsed, setCollapsed] = useState<WorkspaceKind[]>([]);
  const desktop = useMediaQuery('(min-width: 1024px)');
  const [open, setOpen] = useState(() => desktop);
  const [createOpen, setCreateOpen] = useState(false);
  const [renameCanvas, setRenameCanvas] = useState<CreationCanvas | null>(null);
  const [deleteCanvas, setDeleteCanvas] = useState<CreationCanvas | null>(null);
  const [renameTitle, setRenameTitle] = useState('');
  const [mutationPending, setMutationPending] = useState(false);
  const [mutationError, setMutationError] = useState<string>();
  const switcherRef = useRef<HTMLButtonElement>(null);
  const createButtonRef = useRef<HTMLButtonElement>(null);
  const rootRef = useRef<HTMLElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const wasOpen = useRef(false);
  const previousSelectedId = useRef(selectedId);
  const selected = canvases.find(item => item.canvas_id === selectedId);
  const selectedTitle = selected?.title ?? '选择或新建画布';
  const initialKind = selected?.kind ?? 'concept';
  const normalizedSearch = search.trim().toLocaleLowerCase();
  const visibleByKind = useMemo(() => {
    const matches = new Map<WorkspaceKind, CreationCanvas[]>();
    for (const group of groups) {
      matches.set(group.kind, canvases.filter(item =>
        item.kind === group.kind && item.title.toLocaleLowerCase().includes(normalizedSearch),
      ));
    }
    return matches;
  }, [canvases, normalizedSearch]);

  const closeSwitcher = useCallback((deferFocus = false) => {
    setOpen(false);
    setSearch('');
    if (deferFocus) requestAnimationFrame(() => switcherRef.current?.focus());
    else switcherRef.current?.focus();
  }, []);

  useEffect(() => {
    if (desktop || !open || createOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) closeSwitcher(true);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeSwitcher();
      }
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [closeSwitcher, createOpen, desktop, open]);

  useEffect(() => {
    if (!open || createOpen) return;
    if (!wasOpen.current) searchRef.current?.focus();
    wasOpen.current = open;
  }, [createOpen, open]);

  useEffect(() => {
    if (!desktop && previousSelectedId.current !== selectedId) {
      previousSelectedId.current = selectedId;
      setOpen(false);
      setCreateOpen(false);
      setSearch('');
      switcherRef.current?.focus();
    } else {
      previousSelectedId.current = selectedId;
    }
  }, [desktop, selectedId]);

  async function create(kind: WorkspaceKind, title: string) {
    const created = await onCreate(kind, title);
    if (created && !desktop) closeSwitcher();
    return created;
  }

  async function submitRename(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!renameCanvas || !onRename || mutationPending) return;
    const title = renameTitle.trim();
    if (!title) return;
    setMutationPending(true);
    setMutationError(undefined);
    try {
      if (await onRename(renameCanvas, title)) setRenameCanvas(null);
      else setMutationError('重命名未完成，请重试。');
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : '重命名失败，请重试。');
    } finally { setMutationPending(false); }
  }

  async function confirmDelete() {
    if (!deleteCanvas || !onDelete || mutationPending) return;
    setMutationPending(true);
    setMutationError(undefined);
    try {
      if (await onDelete(deleteCanvas)) setDeleteCanvas(null);
      else setMutationError('删除未完成，画布仍保留。请先解决保存提示后重试。');
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : '删除失败，请重试。');
    } finally { setMutationPending(false); }
  }

  const groupList = <div className={desktop
    ? 'min-h-0 flex-1 space-y-3 overflow-y-auto'
    : 'max-h-[35vh] space-y-2 overflow-y-auto'}>
    {groups.map(group => {
      const matches = visibleByKind.get(group.kind) ?? [];
      if (!matches.length) return null;
      const isCollapsed = collapsed.includes(group.kind);
      return <section key={group.kind} aria-label={group.label}>
        <button type="button" aria-expanded={!isCollapsed} onClick={() => setCollapsed(current => isCollapsed
          ? current.filter(value => value !== group.kind)
          : [...current, group.kind])}
          className={desktop
            ? 'mb-1.5 flex w-full items-center gap-1 py-1 text-left text-[11px] text-text-muted'
            : 'flex w-full items-center gap-1 px-1 py-1.5 text-left text-xs text-text-muted'}>
          {isCollapsed ? <ChevronRight size={12} aria-hidden="true" /> : <ChevronDown size={12} aria-hidden="true" />}{group.label}
        </button>
        {!isCollapsed && matches.map(item => <div key={item.canvas_id} className={`group mb-1 flex items-center gap-1 rounded-lg transition-colors px-1 ${desktop ? 'rounded-[7px]' : ''} ${selectedId === item.canvas_id
          ? desktop ? 'bg-[#30342A] text-[#E0EDB4]' : 'bg-white/10 text-white'
          : 'text-text hover:bg-white/5'}`}>
          <button type="button" disabled={mutationPending} onClick={() => { onSelect(item.canvas_id); if (!desktop) closeSwitcher(); }}
            aria-current={selectedId === item.canvas_id ? 'page' : undefined}
            className={`flex min-w-0 flex-1 items-center justify-between gap-2 text-left ${desktop ? 'px-1.5 py-[9px] text-[13px]' : 'px-2 py-2 text-xs'}`}>
            <span className="truncate">{item.title}</span>
            <span className="shrink-0 text-[10px] text-text-muted">{statusLabel[item.status]}</span>
          </button>
          {(onRename || onDelete) && <div className="flex shrink-0 items-center gap-0.5 opacity-100 lg:opacity-0 lg:group-hover:opacity-100 lg:group-focus-within:opacity-100">
            {onRename && <button type="button" aria-label={`重命名画布：${item.title}`} title="重命名画布" disabled={mutationPending} onClick={() => { setMutationError(undefined); setRenameTitle(item.title); setRenameCanvas(item); }} className="rounded p-1 text-text-muted hover:bg-white/10 hover:text-text disabled:opacity-40"><Pencil size={13} aria-hidden="true" /></button>}
            {onDelete && <button type="button" aria-label={`删除画布：${item.title}`} title="删除画布" disabled={mutationPending} onClick={() => { setMutationError(undefined); setDeleteCanvas(item); }} className="rounded p-1 text-text-muted hover:bg-red-500/15 hover:text-red-300 disabled:opacity-40"><Trash2 size={13} aria-hidden="true" /></button>}
          </div>}
        </div>)}
      </section>;
    })}
    {!canvases.length && <p className="px-2 text-xs text-text-muted">还没有画布，从下方开始创作。</p>}
    {canvases.length > 0 && !groups.some(group => (visibleByKind.get(group.kind) ?? []).length > 0) && <div className="space-y-2 px-2 py-1 text-xs text-text-muted">
      <p>没有找到匹配的画布。</p>
      <button type="button" onClick={() => setSearch('')} className="text-accent underline">清除搜索</button>
    </div>}
  </div>;

  const feedback = <>
    {registryError && <div role="alert" className="text-xs text-red-300">{registryError}</div>}
    {registryError && onRetryRegistry && <button type="button" onClick={onRetryRegistry} className="text-left text-xs text-accent underline">重试画布列表</button>}
    {error && !createOpen && <div role="alert" className="text-xs text-red-300">{error}</div>}
    {mutationError && !renameCanvas && !deleteCanvas && <div role="alert" className="text-xs text-red-300">{mutationError}</div>}
    {retry && !createOpen && error && <button type="button" disabled={pending} onClick={() => void retry()} className="text-left text-xs text-accent underline disabled:opacity-40">重试创建</button>}
  </>;

  return <>
    {desktop && !open && <button type="button" aria-label="展开项目画布" aria-expanded={false} onClick={() => setOpen(true)} className="absolute left-3 top-16 z-30 rounded-lg border border-white/10 bg-surface px-3 py-2 text-xs text-text">项目画布 ›</button>}
    <aside ref={rootRef} aria-label="画布工作区" hidden={desktop && !open} inert={desktop && !open}
      className={desktop
        ? `${open ? 'flex' : 'hidden'} z-30 h-full w-[216px] shrink-0 flex-col gap-3 border-r border-white/10 bg-surface p-4 text-text`
        : 'absolute left-4 top-4 z-30 w-64 max-w-[calc(100vw-2rem)] text-text'}>
      {desktop ? <>
        <div className="flex shrink-0 items-center gap-2">
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold">项目画布</h2>
            {selected && <p className="truncate text-[10px] text-text-muted" title={selected.title}>{selected.title}</p>}
          </div>
          <button ref={createButtonRef} type="button" aria-label="新建画布" title="新建画布" disabled={pending} onClick={() => setCreateOpen(true)} className="rounded p-1 text-text-muted hover:bg-white/5 hover:text-text disabled:opacity-40"><Plus size={18} aria-hidden="true" /></button>
          <button type="button" aria-label="收起项目画布栏" title="收起项目画布栏" onClick={() => setOpen(false)} aria-expanded={open} className="rounded p-1 text-text-muted hover:bg-white/5 hover:text-text"><PanelLeftClose size={16} aria-hidden="true" /></button>
        </div>
        <label className="flex shrink-0 items-center gap-2 rounded-[7px] border border-white/[0.06] bg-[#101214] p-2 text-text-muted">
          <Search size={14} aria-hidden="true" />
          <input ref={searchRef} aria-label="搜索画布" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索画布" className="min-w-0 flex-1 bg-transparent text-xs outline-none" />
        </label>
        {groupList}
        {feedback}
      </> : <div className={`rounded-xl border border-white/10 bg-surface/95 shadow-xl backdrop-blur-xl ${open ? 'overflow-hidden' : ''}`}>
        <button ref={switcherRef} type="button" onClick={() => open ? closeSwitcher() : setOpen(true)} aria-expanded={open}
          className="flex w-full items-center justify-between gap-3 rounded-xl border border-white/10 bg-surface/95 px-4 py-3 text-left text-sm font-medium shadow-xl backdrop-blur-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
          <span className="min-w-0 flex-1 truncate" title={selectedTitle}>{selectedTitle}</span>{open ? <ChevronDown size={16} className="shrink-0" /> : <ChevronRight size={16} className="shrink-0" />}
        </button>
        {open && <div className="space-y-3 rounded-b-xl border-x border-b border-white/10 bg-surface/95 px-3 pb-3 shadow-xl backdrop-blur-xl">
          <label className="flex items-center gap-2 rounded-lg border border-white/10 px-2 py-1.5 text-text-muted">
            <Search size={14} aria-hidden="true" />
            <input ref={searchRef} aria-label="搜索画布" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索画布" className="min-w-0 flex-1 bg-transparent text-xs outline-none" />
          </label>
          {groupList}
          <div className="border-t border-white/10 pt-3">
            <button ref={createButtonRef} type="button" onClick={() => setCreateOpen(true)} disabled={pending} className="flex w-full items-center justify-center gap-1 rounded-lg bg-accent px-3 py-2 text-xs text-white hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-40">
              <Plus size={14} aria-hidden="true" />新建画布
            </button>
            {feedback}
          </div>
        </div>}
      </div>}
    </aside>
    <CreateCanvasDialog open={createOpen} initialKind={initialKind} scopeKey={selectedId ?? 'new-workspace'} pending={pending} error={error} finalFocus={createButtonRef} onClose={() => setCreateOpen(false)} onCreate={create} onRetry={retry} />
    <Dialog open={Boolean(renameCanvas)} onOpenChange={next => { if (!next && !mutationPending) { setRenameCanvas(null); setMutationError(undefined); } }}>
      <DialogContent className="border border-white/10 bg-surface text-text" aria-describedby="rename-canvas-description">
        <DialogHeader><DialogTitle>重命名画布</DialogTitle><DialogDescription id="rename-canvas-description">修改画布名称，画布内容不会改变。</DialogDescription></DialogHeader>
        <form onSubmit={event => void submitRename(event)} className="space-y-4">
          <label className="block space-y-1.5 text-xs"><span className="text-text-muted">画布名称</span><input autoFocus aria-label="画布名称" value={renameTitle} maxLength={120} disabled={mutationPending} onChange={event => setRenameTitle(event.target.value)} className="w-full rounded-lg border border-white/10 bg-bg-dark px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50" /></label>
          {mutationError && <div role="alert" className="text-xs text-red-300">{mutationError}</div>}
          <DialogFooter className="p-0"><button type="button" disabled={mutationPending} onClick={() => setRenameCanvas(null)} className="rounded-lg border border-white/10 px-3 py-2 text-xs text-text-muted disabled:opacity-40">取消</button><button type="submit" disabled={mutationPending || !renameTitle.trim()} className="rounded-lg bg-accent px-3 py-2 text-xs text-white disabled:opacity-40">{mutationPending ? '正在保存…' : '保存名称'}</button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
    <Dialog open={Boolean(deleteCanvas)} onOpenChange={next => { if (!next && !mutationPending) { setDeleteCanvas(null); setMutationError(undefined); } }}>
      <DialogContent className="border border-white/10 bg-surface text-text" aria-describedby="delete-canvas-description">
        <DialogHeader><DialogTitle>删除画布</DialogTitle><DialogDescription id="delete-canvas-description">确定删除“{deleteCanvas?.title}”？画布会从项目列表中移除，无法通过侧栏撤销。</DialogDescription></DialogHeader>
        {mutationError && <div role="alert" className="text-xs text-red-300">{mutationError}</div>}
        <DialogFooter className="p-0"><button type="button" disabled={mutationPending} onClick={() => setDeleteCanvas(null)} className="rounded-lg border border-white/10 px-3 py-2 text-xs text-text-muted disabled:opacity-40">取消</button><button type="button" disabled={mutationPending} onClick={() => void confirmDelete()} className="rounded-lg bg-red-600 px-3 py-2 text-xs text-white disabled:opacity-40">{mutationPending ? '正在删除…' : '删除画布'}</button></DialogFooter>
      </DialogContent>
    </Dialog>
  </>;
}
