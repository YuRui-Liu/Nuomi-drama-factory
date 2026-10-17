import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, Plus, Search } from 'lucide-react';
import type { CreationCanvas, WorkspaceKind } from './contracts';
import { CreateCanvasDialog } from './CreateCanvasDialog';

const groups: { kind: WorkspaceKind; label: string }[] = [
  { kind: 'concept', label: '构思画布' }, { kind: 'episode', label: '分集画布' }, { kind: 'free', label: '自由画布' },
];
const statusLabel = { applied: '', running: '创建中', conflict: '创建冲突' };

export function CanvasWorkspaceSwitcher({ canvases, selectedId, onSelect, onCreate, pending, retry, error, registryError, onRetryRegistry }: {
  canvases: CreationCanvas[]; selectedId?: string; onSelect: (id: string) => void;
  onCreate: (kind: WorkspaceKind, title: string) => Promise<boolean>; pending: boolean; retry?: () => Promise<boolean>; error?: string;
  registryError?: string; onRetryRegistry?: () => void;
}) {
  const [search, setSearch] = useState('');
  const [collapsed, setCollapsed] = useState<WorkspaceKind[]>([]);
  const [open, setOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
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
    for (const group of groups) matches.set(group.kind, canvases.filter(item => item.kind === group.kind && item.title.toLocaleLowerCase().includes(normalizedSearch)));
    return matches;
  }, [canvases, normalizedSearch]);

  const closeSwitcher = useCallback(() => {
    setOpen(false);
    setSearch('');
    requestAnimationFrame(() => switcherRef.current?.focus());
  }, []);

  useEffect(() => {
    if (!open || createOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) closeSwitcher();
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
  }, [closeSwitcher, createOpen, open]);

  useEffect(() => {
    if (open && !createOpen && !wasOpen.current) searchRef.current?.focus();
    wasOpen.current = open;
  }, [createOpen, open]);

  useEffect(() => {
    if (previousSelectedId.current !== selectedId) {
      previousSelectedId.current = selectedId;
      setOpen(false);
      setCreateOpen(false);
      setSearch('');
      requestAnimationFrame(() => switcherRef.current?.focus());
    }
  }, [selectedId]);

  async function create(kind: WorkspaceKind, title: string) {
    const created = await onCreate(kind, title);
    if (created) closeSwitcher();
    return created;
  }

  return <>
    <aside ref={rootRef} aria-label="画布工作区" className="absolute left-4 top-4 z-30 w-64 max-w-[calc(100vw-2rem)] text-text">
      <div className={`rounded-xl border border-white/10 bg-surface/95 shadow-xl backdrop-blur-xl ${open ? 'overflow-hidden' : ''}`}>
        <button ref={switcherRef} type="button" onClick={() => open ? closeSwitcher() : setOpen(true)} aria-expanded={open}
          className="flex w-full items-center justify-between gap-3 rounded-xl border border-white/10 bg-surface/95 px-4 py-3 text-left text-sm font-medium shadow-xl backdrop-blur-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
          <span className="min-w-0 flex-1 truncate" title={selectedTitle}>{selectedTitle}</span>{open ? <ChevronDown size={16} className="shrink-0" /> : <ChevronRight size={16} className="shrink-0" />}
        </button>
        {open && <div className="space-y-3 rounded-b-xl border-x border-b border-white/10 bg-surface/95 px-3 pb-3 shadow-xl backdrop-blur-xl">
          <label className="flex items-center gap-2 rounded-lg border border-white/10 px-2 py-1.5 text-text-muted"><Search size={14} aria-hidden="true" />
            <input ref={searchRef} aria-label="搜索画布" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索画布" className="min-w-0 flex-1 bg-transparent text-xs outline-none" />
          </label>
          <div className="max-h-[35vh] space-y-2 overflow-y-auto">
            {groups.map(group => {
              const matches = visibleByKind.get(group.kind) ?? [];
              if (!matches.length) return null;
              const isCollapsed = collapsed.includes(group.kind);
              return <section key={group.kind} aria-label={group.label}>
                <button type="button" aria-expanded={!isCollapsed} onClick={() => setCollapsed(current => isCollapsed ? current.filter(value => value !== group.kind) : [...current, group.kind])} className="flex w-full items-center gap-1 px-1 py-1.5 text-xs text-text-muted">
                  {isCollapsed ? <ChevronRight size={12} aria-hidden="true" /> : <ChevronDown size={12} aria-hidden="true" />}{group.label}
                </button>
                {!isCollapsed && matches.map(item => <button key={item.canvas_id} type="button" onClick={() => { onSelect(item.canvas_id); closeSwitcher(); }} aria-current={selectedId === item.canvas_id ? 'page' : undefined}
                  className={`flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-xs transition-colors ${selectedId === item.canvas_id ? 'bg-white/10 text-white' : 'hover:bg-white/5'}`}>
                  <span className="truncate">{item.title}</span><span className="shrink-0 text-[10px] text-text-muted">{statusLabel[item.status]}</span>
                </button>)}
              </section>;
            })}
            {canvases.length === 0 && <p className="px-2 text-xs text-text-muted">还没有画布，从下方开始创作。</p>}
            {canvases.length > 0 && !groups.some(group => (visibleByKind.get(group.kind) ?? []).length > 0) && <div className="space-y-2 px-2 py-1 text-xs text-text-muted">
              <p>没有找到匹配的画布。</p>
              <button type="button" onClick={() => setSearch('')} className="text-accent underline">清除搜索</button>
            </div>}
          </div>
          <div className="border-t border-white/10 pt-3">
            <button ref={createButtonRef} type="button" onClick={() => setCreateOpen(true)} className="flex w-full items-center justify-center gap-1 rounded-lg bg-accent px-3 py-2 text-xs text-white hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
              <Plus size={14} aria-hidden="true" />新建画布
            </button>
            {registryError && <div role="alert" className="mt-2 text-xs text-red-300">{registryError}</div>}
            {registryError && onRetryRegistry && <button type="button" onClick={onRetryRegistry} className="mt-2 text-xs text-accent underline">重试画布列表</button>}
            {error && !createOpen && <div role="alert" className="mt-2 text-xs text-red-300">{error}</div>}
            {retry && !createOpen && error && <button type="button" disabled={pending} onClick={() => void retry()} className="mt-2 text-xs text-accent underline disabled:opacity-40">重试创建</button>}
          </div>
        </div>}
      </div>
    </aside>
    <CreateCanvasDialog open={createOpen} initialKind={initialKind} pending={pending} error={error} finalFocus={createButtonRef} onClose={() => setCreateOpen(false)} onCreate={create} onRetry={retry} />
  </>;
}
