import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useAuthStore } from '@/stores/auth-store';
import { Button } from '@/components/ui/button';
import { createConversation, creationKey, readCreation, type AssistantConversation } from './conversation-creation';
import { activeConversationKey, conversationEndpoint, conversationOwnerKey } from './conversation-scope';

export type { AssistantConversation } from './conversation-creation';
interface Props {
  project?: string | null;
  canvasId?: string;
  managedWorkspace?: boolean;
  value?: string;
  onChange: (id: string) => void;
  onAvailable?: (ids: string[]) => void;
}
export function ConversationPicker(props: Props) {
  const user = useAuthStore(state => state.username) || '';
  const identity = conversationOwnerKey({ user, project: props.project, canvas: props.managedWorkspace ? props.canvasId : undefined });
  return <PickerInstance key={`${Boolean(props.managedWorkspace)}:${identity}`} {...props} user={user} />;
}
function PickerInstance({ project, canvasId, managedWorkspace = false, value = 'default', onChange, onAvailable, user }: Props & { user: string }) {
  const owner = useMemo(() => ({ user, project, canvas: managedWorkspace ? canvasId : undefined }), [user, project, canvasId, managedWorkspace]);
  const current = useRef(true);
  useEffect(() => { current.current = true; return () => { current.current = false; }; }, []);
  const ready = !managedWorkspace || Boolean(user && project && canvasId);
  const list = useQuery({
    queryKey: ['assistant-conversations', conversationOwnerKey(owner)],
    queryFn: ({ signal }) => api.get(conversationEndpoint(owner), { signal }).json<{ data: AssistantConversation[] }>(),
    enabled: ready, refetchInterval: 10_000, gcTime: 0,
  });
  const items = useMemo(() => (list.data?.data ?? (managedWorkspace ? [] : [{ id: 'default', title: '默认会话', running: false }]))
    .filter(item => !managedWorkspace || ('canvas_id' in item && item.canvas_id === canvasId && item.id !== 'default')), [list.data, managedWorkspace, canvasId]);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState('新会话');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [creation, setCreation] = useState<ReturnType<typeof readCreation>>(null);
  const recoverable = Boolean(creation && (!creation.result || !list.isSuccess || !items.some(item => item.id === creation.result!.id)));
  const callbacks = useRef({ onChange, onAvailable });
  const restored = useRef(false);
  callbacks.current = { onChange, onAvailable };
  useEffect(() => {
    const read = () => {
      try { setCreation(readCreation(owner)); }
      catch (failure) { setError(String(failure)); }
    };
    const onStorage = (event: StorageEvent) => { if (event.key === creationKey(owner)) read(); };
    read();
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [owner, list.data]);
  useEffect(() => {
    if (!list.isSuccess) return;
    const ids = items.map(item => item.id);
    callbacks.current.onAvailable?.(ids);
    if (restored.current && ids.includes(value)) return;
    try {
      const saved = localStorage.getItem(activeConversationKey(owner));
      const next = saved && ids.includes(saved) ? saved : (ids.includes(value) ? value : managedWorkspace ? '' : ids[0] || 'default');
      restored.current = true;
      callbacks.current.onChange(next);
    } catch (failure) { setError(String(failure)); }
  }, [items, list.isSuccess, owner, value, managedWorkspace]);
  const select = (id: string) => {
    if (!items.some(item => item.id === id)) return;
    try { localStorage.setItem(activeConversationKey(owner), id); onChange(id); }
    catch { setError('无法保存当前会话选择，请检查浏览器存储。'); }
  };
  const create = async (resume = false) => {
    setBusy(true); setError('');
    try {
      const result = await createConversation(owner, resume ? undefined : title, () => current.current && (useAuthStore.getState().username || '') === user);
      if (!current.current) return;
      const refreshed = await list.refetch();
      if (!current.current) return;
      if (!refreshed.data?.data.some(item => item.id === result.id && (!managedWorkspace || item.canvas_id === canvasId))) {
        throw new Error('会话已创建，列表尚未确认，请恢复原请求。');
      }
      localStorage.setItem(activeConversationKey(owner), result.id);
      onChange(result.id); setCreating(false); setTitle('新会话'); setCreation(readCreation(owner));
    } catch (failure) {
      if (current.current) {
        setError(failure instanceof Error ? failure.message : String(failure));
        try { setCreation(readCreation(owner)); } catch { /* Keep the storage error visible. */ }
      }
    } finally { if (current.current) setBusy(false); }
  };
  return <div className={`space-y-1 border-b px-3 ${managedWorkspace ? 'py-1' : 'py-2'}`} aria-label="助手会话管理">
    <div className="flex items-center gap-2">
      <label className="flex min-w-0 flex-1 items-center gap-2 text-xs">会话
        <select aria-label="切换助手会话" className="min-h-9 min-w-0 flex-1 rounded border bg-background px-2 py-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" value={value} onChange={event => select(event.target.value)}>
          {managedWorkspace && <option value="">{items.length ? '请选择会话' : '此画布暂无会话'}</option>}
          {items.map(item => <option key={item.id} value={item.id}>{item.title}{item.running ? ' · 运行中' : ''}</option>)}
        </select>
      </label>
      <Button size="sm" variant="outline" className="min-h-9" disabled={!ready || busy} onClick={() => setCreating(true)}>新建会话</Button>
    </div>
    <p className={`text-[10px] text-muted-foreground ${managedWorkspace ? 'sr-only' : ''}`}>各会话独立保存上下文；切换会话不会停止正在执行的任务。</p>
    {creating && <form className="flex flex-wrap gap-2" onSubmit={event => { event.preventDefault(); void create(); }}>
      <input aria-label="新会话名称" className="min-w-0 flex-1 rounded border bg-background p-1.5 text-sm" value={title} maxLength={100} required disabled={busy} onChange={event => setTitle(event.target.value)} />
      <Button size="sm" type="submit" disabled={busy || !title.trim()}>{busy ? '创建中…' : '创建'}</Button>
      <Button size="sm" type="button" variant="ghost" disabled={busy} onClick={() => setCreating(false)}>取消</Button>
    </form>}
    {recoverable && <Button size="sm" variant="outline" disabled={busy || !ready} onClick={() => void create(true)}>恢复上次创建</Button>}
    {list.isError && <p role="alert" className="text-xs">会话列表加载失败。<button className="underline" onClick={() => void list.refetch()}>重试</button></p>}
    {error && <p role="alert" className="text-xs">{error}</p>}
  </div>;
}
