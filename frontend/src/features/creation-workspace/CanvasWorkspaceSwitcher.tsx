import { useState } from 'react';
import { ChevronDown, ChevronRight, Plus, Search } from 'lucide-react';
import type { CreationCanvas, WorkspaceKind } from './contracts';

const groups: { kind: WorkspaceKind; label: string }[] = [
  { kind: 'concept', label: '构思画布' }, { kind: 'episode', label: '分集画布' }, { kind: 'free', label: '自由画布' },
];
const statusLabel = { applied: '', running: '创建中', conflict: '创建冲突' };
export function CanvasWorkspaceSwitcher({ canvases, selectedId, onSelect, onCreate, pending, retry, error }: {
  canvases: CreationCanvas[]; selectedId?: string; onSelect: (id: string) => void;
  onCreate: (kind: WorkspaceKind, title: string) => Promise<boolean>; pending: boolean; retry?: () => void; error?: string;
}) {
  const [search, setSearch] = useState('');
  const [collapsed, setCollapsed] = useState<WorkspaceKind[]>([]);
  const [kind, setKind] = useState<WorkspaceKind>('concept');
  const [title, setTitle] = useState('');
  const [open, setOpen] = useState(true);
  return <aside aria-label="画布工作区" className="absolute left-4 top-4 z-30 w-64 rounded-2xl border border-white/10 bg-surface/95 text-text shadow-xl backdrop-blur-xl">
    <button type="button" onClick={() => setOpen(value => !value)} aria-expanded={open} className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium">
      <span>项目画布</span>{open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
    </button>
    {open && <div className="space-y-3 px-3 pb-3">
      <label className="flex items-center gap-2 rounded-lg border border-white/10 px-2 py-1.5 text-text-muted"><Search size={14} />
        <input aria-label="搜索画布" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索画布" className="min-w-0 flex-1 bg-transparent text-xs outline-none" />
      </label>
      <div className="max-h-[35vh] space-y-2 overflow-y-auto">
        {groups.map(group => <section key={group.kind}>
          <button type="button" aria-expanded={!collapsed.includes(group.kind)} onClick={() => setCollapsed(current => current.includes(group.kind) ? current.filter(value => value !== group.kind) : [...current, group.kind])} className="flex w-full items-center gap-1 px-1 py-1.5 text-xs text-text-muted">
            {collapsed.includes(group.kind) ? <ChevronRight size={12} /> : <ChevronDown size={12} />}{group.label}
          </button>
          {!collapsed.includes(group.kind) && canvases.filter(item => item.kind === group.kind && item.title.toLocaleLowerCase().includes(search.toLocaleLowerCase())).map(item => <button key={item.canvas_id} type="button" onClick={() => onSelect(item.canvas_id)} aria-current={selectedId === item.canvas_id ? 'page' : undefined}
            className={`flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-xs transition-colors ${selectedId === item.canvas_id ? 'bg-white/10 text-white' : 'hover:bg-white/5'}`}>
            <span className="truncate">{item.title}</span><span className="shrink-0 text-[10px] text-text-muted">{statusLabel[item.status]}</span>
          </button>)}
        </section>)}
        {!canvases.length && <p className="px-2 text-xs text-text-muted">还没有画布，从下方开始创作。</p>}
      </div>
      <form onSubmit={event => { event.preventDefault(); if (title.trim() && !pending) void onCreate(kind, title.trim()).then(created => { if (created) setTitle(''); }); }} className="space-y-2 border-t border-white/10 pt-3">
        <select aria-label="画布类型" value={kind} onChange={event => setKind(event.target.value as WorkspaceKind)} disabled={pending} className="w-full rounded-lg border border-white/10 bg-bg-dark px-2 py-2 text-xs">
          {groups.map(group => <option key={group.kind} value={group.kind}>{group.label}</option>)}
        </select>
        <input aria-label="画布名称" value={title} onChange={event => setTitle(event.target.value)} disabled={pending} required maxLength={120} placeholder="输入画布名称" className="w-full rounded-lg border border-white/10 bg-bg-dark px-2 py-2 text-xs" />
        <button type="submit" disabled={pending || !title.trim()} className="flex w-full items-center justify-center gap-1 rounded-lg bg-accent px-3 py-2 text-xs text-white disabled:opacity-40"><Plus size={14} />{pending ? '正在创建…' : '创建画布'}</button>
      </form>
      {error && <div role="alert" className="text-xs text-red-300">{error}</div>}
      {retry && <button type="button" disabled={pending} onClick={retry} className="text-xs text-accent underline">重试创建</button>}
    </div>}
  </aside>;
}
