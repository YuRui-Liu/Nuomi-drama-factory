import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { getTeamResources, saveTeamDraft } from './api';
import { draftData, resolveMethod, type ResourceVersion } from './types';
import { read, publish, resourceVersion, teamPath, errorText, type BuiltinMethod } from './tools-api';
import type { TeamToolsProps } from './team-tools';

export function ResourceLibrary(props: TeamToolsProps) {
  const { project, overview, selectedRole: role, selectedSubtask: task, disabled, onChanged } = props;
  const client = useQueryClient();
  const resources = useQuery({ queryKey: ['agent-team-resources'], queryFn: getTeamResources });
  const builtin = useQuery({ queryKey: ['agent-team-builtins'], queryFn: () => read<BuiltinMethod[]>('api/v1/agent-team-builtin-methods') });
  const [selected, setSelected] = useState<ResourceVersion | null>(null);
  const [content, setContent] = useState('');
  const [kind, setKind] = useState<ResourceVersion['kind']>('skill');
  const [revision, setRevision] = useState(1);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [usage, setUsage] = useState<unknown>(null);
  const dirty = content !== (selected?.content ?? '') || kind !== (selected?.kind ?? 'skill');
  useEffect(() => { props.onEditingChange?.(dirty || busy); return () => props.onEditingChange?.(false); }, [dirty, busy, props.onEditingChange]);
  const run = async (action: () => Promise<unknown>) => { setBusy(true); setMessage(''); try { await action(); } catch (e) { setMessage(errorText(e)); } finally { setBusy(false); } };
  const choose = (r: ResourceVersion | null) => { setSelected(r); setContent(r?.content ?? ''); setKind(r?.kind ?? 'skill'); setRevision(r?.revision ?? 1); setUsage(null); };
  const save = async (copy: boolean) => {
    const id = copy || !selected ? crypto.randomUUID() : selected.id;
    const expected = copy || !selected ? 0 : selected.revision;
    const value = await publish<ResourceVersion>(`api/v1/agent-team-resources/${encodeURIComponent(id)}`, { id, revision: expected + 1, kind, content, archived: false }, expected);
    choose(value); await client.invalidateQueries({ queryKey: ['agent-team-resources'] }); setMessage('已发布不可变版本。现有固定引用保持原版本。');
  };
  const apply = async () => {
    if (!selected || selected.archived || dirty) return;
    const data = draftData(overview);
    const current = resolveMethod(overview.template, data, role, task);
    const field = selected.kind === 'prompt' ? 'prompt' : selected.kind === 'skill' ? 'skills' : 'references';
    const value = field === 'prompt' ? selected.content : [...current[field].filter(r => r.id !== selected.id), { id: selected.id, revision: selected.revision }];
    data.overrides[role] = { ...data.overrides[role], [task]: { ...data.overrides[role]?.[task], [field]: value } };
    await saveTeamDraft(project, data, overview.draft?.draft_revision ?? 0); onChanged(); setMessage(`已保存到 ${role} / ${task} 草稿，尚未启用。`);
  };
  return <section className="space-y-4 text-sm"><div className="rounded border border-lime-900 p-3"><h3 className="font-medium text-lime-300">当前内置方法与固定协议</h3><p className="mt-1 text-xs text-zinc-400">以下来自真实运行源码。协议包含结构约束，只读；运行时输入将在执行时拼接。写作技法可复制为个人资源，再固定到项目方法。</p>{builtin.isError && <p role="alert">内置方法读取失败 <Button onClick={() => void builtin.refetch()}>重试</Button></p>}{builtin.data?.filter(b => b.role_id === role && b.subtask_id === task).map(b => <details key={b.id} className="mt-3"><summary className="cursor-pointer">{b.name} · {b.kind === 'protocol' ? '固定协议 · 只读' : '创作技法'}</summary><p className="my-2 break-all text-xs text-zinc-500">{b.source}<br />SHA256 {b.content_hash}</p><pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-zinc-900 p-3 text-xs">{b.content || '当前部署缺少参考文件，未加载技法正文。'}</pre>{b.replaceable && <Button disabled={busy || dirty || !b.content} onClick={() => { choose(null); setKind('skill'); setContent(b.content); setMessage('已复制到编辑区，发布后方可固定引用。'); }}>复制技法到个人资源编辑区</Button>}</details>)}</div>
    <h3 className="font-medium">个人资源库</h3><p className="text-xs text-zinc-400">提示词应用为项目文本覆盖；技法与参考资料固定到明确版本。归档资源不能新增引用。</p>
    {resources.isError && <p role="alert">资源加载失败 <Button onClick={() => void resources.refetch()}>重试</Button></p>}
    <div className="flex flex-wrap gap-2"><select aria-label="选择资源" disabled={busy || dirty} className="max-w-full rounded bg-zinc-900 p-2" value={selected?.id ?? ''} onChange={e => choose(resources.data?.find(r => r.id === e.target.value) ?? null)}><option value="">新建资源</option>{resources.data?.map(r => <option key={r.id} value={r.id}>{r.id} · {r.kind} · v{r.revision}{r.archived ? ' · 已归档' : ''}</option>)}</select>{dirty && <Button variant="outline" disabled={busy} onClick={() => choose(selected)}>放弃资源编辑</Button>}</div>
    {selected && <div className="flex flex-wrap items-center gap-2"><label>历史版本 <input aria-label="资源版本" className="w-20 bg-zinc-900 p-2" type="number" min={1} value={revision} onChange={e => setRevision(Number(e.target.value))} /></label><Button disabled={busy || dirty} onClick={() => void run(async () => choose(await resourceVersion(selected.id, revision)))}>读取历史版本</Button><Button disabled={busy} onClick={() => void run(async () => { setUsage(await Promise.all([read(`api/v1/agent-team-resources/${encodeURIComponent(selected.id)}/usage`), read(`${teamPath(project)}/resources/${encodeURIComponent(selected.id)}/usage`)])); })}>查看实际引用</Button><p className="w-full break-all text-xs text-zinc-500">查看 v{selected.revision} · {selected.archived ? '已归档' : '可引用'} · {selected.content_hash}</p></div>}
    <label className="block">资源类型 <select aria-label="资源类型" disabled={busy || !!selected} value={kind} onChange={e => setKind(e.target.value as ResourceVersion['kind'])} className="rounded bg-zinc-900 p-2"><option value="skill">技法 Skill</option><option value="reference">参考资料</option><option value="prompt">提示词</option></select></label><textarea aria-label="资源正文" disabled={busy} value={content} onChange={e => setContent(e.target.value)} className="min-h-48 w-full rounded border border-zinc-800 bg-zinc-900 p-3" />
    <div className="flex flex-wrap gap-2"><Button disabled={busy || !content.trim()} onClick={() => void run(() => save(false))}>{selected ? `发布新修订 v${selected.revision + 1}` : '创建并发布资源'}</Button>{selected && <Button disabled={busy || !content.trim()} onClick={() => void run(() => save(true))}>复制为新资源</Button>}<Button disabled={disabled || busy || dirty || !selected || selected.archived} onClick={() => void run(apply)}>将此版本用于当前方法草稿</Button></div>
    {usage !== null && <details open><summary>模板引用 / 当前项目引用</summary><pre className="max-h-48 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(usage, null, 2)}</pre></details>}{message && <p role="status" className="text-amber-200">{message}</p>}
  </section>;
}
