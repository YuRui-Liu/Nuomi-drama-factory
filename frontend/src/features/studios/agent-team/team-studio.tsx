import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { BackendStatusError } from '@/lib/api-errors';
import { getTeam, getTeamResources, saveTeamDraft } from './api';
import { announceTeamChange, useTeam } from './queries';
import { defaultMethod, draftData, resolveMethod, type DraftData, type MethodConfig, type TeamOverview } from './types';
import { RoleOverview, taskLabels } from './role-overview';
import { MethodEditor } from './method-editor';
import { VersionPanel } from './version-panel';
import { TeamTools } from './team-tools';

export function TeamStudio({ project }: { project: string }) {
  const query = useTeam(project);
  const resources = useQuery({ queryKey: ['agent-team-resources'], queryFn: getTeamResources });
  const [base, setBase] = useState<TeamOverview | null>(null);
  const [data, setData] = useState<DraftData | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState<TeamOverview | null>(null);
  const [role, setRole] = useState('writer');
  const [task, setTask] = useState('brief');
  const [mobileDetail, setMobileDetail] = useState(false);
  const [view, setView] = useState<'method' | 'tools'>('method');
  const [toolsEditing, setToolsEditing] = useState(false);
  const [resourceViewRequest, setResourceViewRequest] = useState(0);
  const generation = useRef(0);
  const savingRef = useRef(false);
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;
  useEffect(() => { if (query.data && !dirtyRef.current && !savingRef.current) { setBase(query.data); setData(draftData(query.data)); } }, [query.data]);
  useEffect(() => { window.dispatchEvent(new CustomEvent('studio-dirty', { detail: { module: 'agent-team', dirty: dirty || toolsEditing } })); }, [dirty, toolsEditing]);
  const save = useCallback(async () => {
    if (toolsEditing) { setError('请先完成、发布或放弃资源与模板编辑；试运行提交未确认时请先重试确认。'); setView('tools'); window.dispatchEvent(new Event('studio-save-failed-agent-team')); return; }
    if (!data || !base || savingRef.current || !query.isSuccess || query.isFetching || conflict) { window.dispatchEvent(new Event('studio-save-failed-agent-team')); return; }
    savingRef.current = true; setSaving(true); setError('');
    const sentGeneration = generation.current;
    try {
      const saved = await saveTeamDraft(project, structuredClone(data), base.draft?.draft_revision ?? 0);
      setBase(previous => previous ? { ...previous, draft: saved } : previous);
      if (sentGeneration === generation.current) { dirtyRef.current = false; setDirty(false); }
      else window.dispatchEvent(new Event('studio-save-failed-agent-team'));
      announceTeamChange(project);
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败');
      if (e instanceof BackendStatusError && e.status === 409) { try { setConflict(await getTeam(project)); } catch { setError('版本冲突；读取服务器版本失败。编辑已保留，请重试刷新。'); } }
      window.dispatchEvent(new Event('studio-save-failed-agent-team'));
    } finally { savingRef.current = false; setSaving(false); }
  }, [data, base, project, query.isSuccess, query.isFetching, conflict, toolsEditing]);
  useEffect(() => { const listener = () => void save(); window.addEventListener('studio-save-agent-team', listener); return () => window.removeEventListener('studio-save-agent-team', listener); }, [save]);
  const change = <K extends keyof MethodConfig>(field: K, value: MethodConfig[K] | undefined) => { generation.current += 1; dirtyRef.current = true; setDirty(true); setData(previous => { if (!previous) return previous; const next = structuredClone(previous); const fields = { ...next.overrides[role]?.[task] }; if (value === undefined) delete fields[field]; else Object.assign(fields, { [field]: value }); next.overrides[role] = { ...next.overrides[role], [task]: fields }; return next; }); };
  if (!base || !data) return <div className="p-8" role={query.isError ? 'alert' : 'status'}>{query.isError ? <><p>团队配置加载失败。</p><Button onClick={() => void query.refetch()}>重试</Button></> : '正在加载团队…'}</div>;
  const method = resolveMethod(base.template, data, role, task);
  const modifiedDisconnected = base.catalog.some(r => r.subtasks.some(t => !base.connectivity[r.id]?.[t] && (Object.keys(data.overrides[r.id]?.[t] ?? {}).length > 0 || JSON.stringify(resolveMethod(base.template, data, r.id, t)) !== JSON.stringify(defaultMethod))));
  const unavailable = !query.isSuccess || query.isFetching;
  const overrideCount = Object.values(data.overrides).reduce((n, tasks) => n + Object.values(tasks).reduce((m, fields) => m + Object.keys(fields).length, 0), 0);
  const refresh = () => announceTeamChange(project);
  return <div className="min-h-full bg-zinc-950 text-zinc-100">
    <header className="sticky top-0 z-10 flex flex-wrap items-center gap-4 border-b border-zinc-800 bg-zinc-950/95 px-5 py-4"><div className="mr-auto"><p className="text-[10px] tracking-[.2em] text-lime-300">AGENT TEAM</p><h1 className="text-lg font-semibold">创作团队</h1><p className="text-xs text-zinc-400">{base.template.name} · 模板 v{base.template.revision} · {overrideCount} 项项目覆盖</p></div><div className="text-xs text-zinc-400">草稿 v{base.draft?.draft_revision ?? 0} {dirty && '· 未保存'}<br />{base.active ? `已启用 v${base.active.active_revision}` : '尚未启用 · 沿用现有方法'}</div><Button className="bg-lime-300 text-zinc-950 hover:bg-lime-200" disabled={!dirty || saving || unavailable || !!conflict} onClick={() => void save()}>{saving ? '保存中…' : '保存草稿'}</Button></header>
    {error && <div role="alert" className="border-b border-amber-700 p-4 text-sm text-amber-300">{error}</div>}
    {query.isError && <div role="alert" className="p-4">配置刷新失败，编辑已保留。<Button onClick={() => void query.refetch()}>重试</Button></div>}
    {conflict && <div role="alert" className="space-y-2 border-b border-amber-700 p-4 text-sm"><p>服务器草稿已更新至 v{conflict.draft?.draft_revision ?? 0}，本地文字仍保留。重新加载将放弃本地编辑。</p><Button variant="outline" onClick={() => { setBase(conflict); setData(draftData(conflict)); setConflict(null); dirtyRef.current = false; setDirty(false); setError(''); }}>放弃本地修改并加载服务器版本</Button></div>}
    <div className="grid lg:grid-cols-[260px_minmax(0,1fr)]">
      <aside className={`${mobileDetail ? 'hidden lg:block' : ''} border-r border-zinc-800 p-5`}>
        <RoleOverview overview={base} role={role} task={task} onSelect={(r,t) => {
          if (toolsEditing && (r !== role || t !== task)) { setError('请先完成或放弃工具面板中的编辑，再切换子任务。'); return; }
          setRole(r); setTask(t); setMobileDetail(true);
        }} />
      </aside>
      <main className={`${!mobileDetail ? 'hidden lg:block' : ''} min-w-0 space-y-6 p-5 lg:p-8`}>
        <Button className="lg:hidden" variant="ghost" onClick={() => setMobileDetail(false)}>← 返回角色列表</Button>
        <div><p className="text-xs text-lime-300">{base.catalog.find(r => r.id === role)?.name}</p><h2 className="text-xl font-semibold">{taskLabels[task] ?? task}</h2><p className="mt-2 text-xs text-zinc-400">{base.connectivity[role]?.[task] ? '已接入制作流程 · 启用后应用于新任务' : '此子任务尚未接入；可以保存方法草稿，但不能启用自定义方法。'}</p></div>
        <div role="tablist" aria-label="方法工作区" className="flex flex-wrap gap-2 border-b border-zinc-800 pb-3">
          <button role="tab" aria-selected={view === 'method'} onClick={() => setView('method')} className={`rounded px-4 py-2 text-sm ${view === 'method' ? 'bg-zinc-800 text-lime-300' : 'text-zinc-400'}`}>方法编辑</button>
          <button role="tab" aria-selected={view === 'tools'} onClick={() => setView('tools')} className={`rounded px-4 py-2 text-sm ${view === 'tools' ? 'bg-zinc-800 text-lime-300' : 'text-zinc-400'}`}>方法与资源</button>
        </div>
        <div hidden={view !== 'method'} className="space-y-6">
          <div className="flex flex-wrap items-center gap-3 rounded border border-zinc-800 p-3 text-xs text-zinc-400"><p>未覆盖时沿用内置创作方法；固定输出协议始终保留。</p><Button variant="outline" onClick={() => { setResourceViewRequest(n => n + 1); setView('tools'); }}>查看当前内置方法与来源</Button></div>
          {toolsEditing && <p className="text-xs text-amber-200">工具面板有未完成编辑，请先发布、放弃或确认提交。</p>}
          <MethodEditor role={role} method={method} overrides={data.overrides[role]?.[task] ?? {}} resources={resources.data ?? []} resourceSnapshots={[...(base.draft?.data.resources ?? []), ...(base.active?.snapshot.resources ?? [])]} disabled={unavailable || !!conflict || toolsEditing} onChange={change} onRestore={field => change(field, undefined)} />
          {resources.isError && <p role="alert" className="text-xs text-amber-300">资源库读取失败，无法选择新资源；已固定引用仍保留。</p>}
          {modifiedDisconnected && <p className="text-xs text-amber-300">未接入子任务含自定义配置，暂不能启用团队。</p>}
          <VersionPanel project={project} overview={base} disabled={dirty || unavailable || saving || !!conflict || toolsEditing} activationDisabled={modifiedDisconnected} onChanged={refresh} />
        </div>
        <div hidden={view !== 'tools'}><TeamTools project={project} overview={base} disabled={dirty || unavailable || saving || !!conflict} selectedRole={role} selectedSubtask={task} onChanged={refresh} onEditingChange={setToolsEditing} resourceViewRequest={resourceViewRequest} /></div>
      </main>
    </div>
  </div>;
}
