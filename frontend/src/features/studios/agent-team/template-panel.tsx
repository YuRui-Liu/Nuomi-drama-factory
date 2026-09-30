import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import type { TeamToolsProps } from './team-tools';
import type { TeamTemplate } from './types';
import { templates, templateVersion, upgrade, post, publish, teamPath, errorText } from './tools-api';
import { taskLabels } from './role-overview';

const labels: Record<string, string> = { writer: '编剧', script_parser: '剧本解析', director: '分镜导演', video_director: '视频导演', model: '模型路由', prompt: '工作提示词', skills: '技法', references: '参考资料', director_preferences: '导演偏好' };
const fieldLabel = (field: string) => field.split(' / ').map(part => labels[part] ?? taskLabels[part] ?? part).join(' / ');

export function templateChanges(before: TeamTemplate, after: TeamTemplate) {
  const changes: string[] = [];
  for (const role of new Set([...Object.keys(before.roles), ...Object.keys(after.roles)]))
    for (const task of new Set([...Object.keys(before.roles[role] ?? {}), ...Object.keys(after.roles[role] ?? {})]))
      for (const field of new Set([...Object.keys(before.roles[role]?.[task] ?? {}), ...Object.keys(after.roles[role]?.[task] ?? {})]))
        if (JSON.stringify((before.roles[role]?.[task] as unknown as Record<string, unknown>)?.[field]) !== JSON.stringify((after.roles[role]?.[task] as unknown as Record<string, unknown>)?.[field])) changes.push(`${role} / ${task} / ${field}`);
  return changes;
}
export function TemplatePanel({ project, overview, disabled, onChanged, onEditingChange }: TeamToolsProps) {
  const client = useQueryClient();
  const list = useQuery({ queryKey: ['agent-team-templates'], queryFn: templates });
  const [selected, setSelected] = useState<TeamTemplate | null>(null);
  const [revision, setRevision] = useState(1);
  const [name, setName] = useState('我的团队');
  const [roles, setRoles] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const dirty = !!selected && (roles !== JSON.stringify(selected.roles, null, 2) || name !== selected.name);
  useEffect(() => { onEditingChange?.(dirty || busy); return () => onEditingChange?.(false); }, [dirty, busy, onEditingChange]);
  const run = async (action: () => Promise<unknown>) => { setBusy(true); setMessage(''); try { await action(); await client.invalidateQueries({ queryKey: ['agent-team-templates'] }); onChanged(); setMessage('操作完成。项目方法仅更新草稿，需另行启用。'); } catch (e) { setMessage(errorText(e)); } finally { setBusy(false); } };
  return <section className="text-sm"><fieldset disabled={busy} className="space-y-3"><p className="text-zinc-400">当前固定 {overview.template.name} v{overview.template.revision}。选择其他版本前先比较变更；项目覆盖完整保留。</p>
    {list.isError && <p role="alert">模板加载失败 <Button onClick={() => void list.refetch()}>重试</Button></p>}
    <Button variant="outline" disabled={busy} onClick={() => void list.refetch()}>刷新模板列表（保留编辑）</Button>
    {dirty && <div className="flex flex-wrap gap-2"><p>模板有未发布编辑，切换模板前请先发布或放弃。</p><Button disabled={busy} onClick={() => { setRoles(JSON.stringify(selected!.roles, null, 2)); setName(selected!.name); }}>放弃模板编辑</Button></div>}
    {selected && <details><summary>查看模板更新前后的完整字段</summary><div className="grid gap-2 xl:grid-cols-2"><div><h4 className="my-2 font-medium">当前模板 · v{overview.template.revision}</h4><pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(overview.template.roles, null, 2)}</pre></div><div><h4 className="my-2 font-medium">所选版本 · v{selected.revision}</h4><pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(selected.roles, null, 2)}</pre></div></div></details>}
    <label className="block">模板<select aria-label="选择模板" className="ml-3 max-w-full rounded bg-zinc-900 p-2" disabled={busy || dirty} value={selected?.id ?? ''} onChange={e => { const t = list.data?.find(t => t.id === e.target.value) ?? null; setSelected(t); setRevision(t?.revision ?? 1); setRoles(t ? JSON.stringify(t.roles, null, 2) : ''); setName(t?.name ?? '我的团队'); }}><option value="">选择模板</option>{list.data?.map(t => <option key={t.id} value={t.id}>{t.name} · v{t.revision}</option>)}</select></label>
    {selected && <><div className="flex flex-wrap items-center gap-2"><label>固定版本 <input aria-label="模板版本" type="number" min={1} value={revision} className="w-20 bg-zinc-900 p-2" onChange={e => setRevision(Number(e.target.value))} /></label><Button disabled={busy || dirty} onClick={() => void run(async () => { const t = await templateVersion(selected.id, revision); setSelected(t); setName(t.name); setRoles(JSON.stringify(t.roles, null, 2)); })}>读取版本</Button></div><div className="rounded border border-zinc-800 p-3"><p>应用 {selected.name} v{selected.revision} 的字段差异</p>{templateChanges(overview.template, selected).length ? <ul className="mt-2 list-inside list-disc text-xs text-zinc-400">{templateChanges(overview.template, selected).map(field => <li key={field}>{fieldLabel(field)} {overview.draft?.data.overrides[field.split(' / ')[0]]?.[field.split(' / ')[1]]?.[field.split(' / ')[2] as keyof import('./types').MethodConfig] !== undefined ? '（项目覆盖保留）' : ''}</li>)}</ul> : <p className="text-xs text-zinc-400">没有模板字段变化</p>}<Button className="mt-3" disabled={disabled || busy || dirty} onClick={() => void run(() => upgrade(project, selected, overview.draft?.draft_revision ?? 0))}>将此固定版本应用到草稿</Button></div></>}
    <label className="block">新模板名称<input aria-label="模板名称" value={name} onChange={e => setName(e.target.value)} className="ml-3 rounded bg-zinc-900 p-2" /></label><div className="flex flex-wrap gap-2"><Button disabled={disabled || busy || !name.trim()} onClick={() => void run(() => post(`${teamPath(project)}/copy-template-from-project`, { new_id: crypto.randomUUID(), name }))}>复制项目为模板</Button><Button disabled={busy || !selected || !name.trim()} onClick={() => void run(() => post(`api/v1/agent-team-templates/${encodeURIComponent(selected!.id)}/copy`, { new_id: crypto.randomUUID(), name, revision: selected!.revision }))}>复制选中模板</Button></div>
    {selected && selected.id !== 'builtin' && <details><summary className="cursor-pointer text-lime-300">编辑并发布新修订</summary><p className="my-2 text-xs text-zinc-400">发布不会更新任何项目固定版本。保留 JSON 字段格式；模型和方法须满足校验。</p><textarea aria-label="模板方法 JSON" className="h-64 w-full rounded bg-zinc-900 p-3 font-mono text-xs" value={roles} onChange={e => setRoles(e.target.value)} /><Button disabled={busy || !name.trim()} onClick={() => void run(async () => { const next = await publish<TeamTemplate>(`api/v1/agent-team-templates/${encodeURIComponent(selected.id)}`, { ...selected, name, roles: JSON.parse(roles), revision: selected.revision + 1 }, selected.revision); setSelected(next); setRevision(next.revision); setName(next.name); setRoles(JSON.stringify(next.roles, null, 2)); })}>发布新修订 v{selected.revision + 1}</Button></details>}
    {message && <p role="status" className="text-amber-200">{message}</p>}
  </fieldset></section>;
}
