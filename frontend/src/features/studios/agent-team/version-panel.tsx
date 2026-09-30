import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog';
import { activateTeam, getTeamDiff, getTeamVersions, rollbackTeam } from './api';
import { teamKey } from './queries';
import type { TeamOverview } from './types';
import { taskLabels } from './role-overview';
export function VersionPanel({ project, overview, disabled, onChanged }: { project: string; overview: TeamOverview; disabled: boolean; onChanged: () => void }) {
  const diff = useQuery({ queryKey: [...teamKey(project), 'diff', overview.draft?.draft_revision, overview.active?.active_revision], queryFn: () => getTeamDiff(project) });
  const versions = useQuery({ queryKey: [...teamKey(project), 'versions', overview.active?.active_revision], queryFn: () => getTeamVersions(project) });
  const [action, setAction] = useState<number | 'activate' | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const blocked = disabled || pending || !overview.draft || !diff.isSuccess || !versions.isSuccess;
  const submit = async () => { if (action === null || blocked) return; setPending(true); setError(''); try { const args = [project, overview.draft!.draft_revision, overview.active?.active_revision ?? 0] as const; if (action === 'activate') await activateTeam(...args); else await rollbackTeam(project, action, args[1], args[2]); setAction(null); onChanged(); } catch (e) { setError(e instanceof Error ? e.message : '版本操作失败，请重新加载后重试'); } finally { setPending(false); } };
  return <section className="space-y-3 rounded-xl border border-zinc-800 p-4"><h2 className="text-sm font-semibold">草稿与启用版本</h2><p className="text-xs text-zinc-400">保存仅更新草稿；启用只影响之后创建的任务。</p>{error && <p role="alert">{error}</p>}{(diff.isError || versions.isError) && <p role="alert">无法读取版本，请刷新后再操作。</p>}<details><summary className="cursor-pointer text-sm">查看待启用差异（{diff.data?.length ?? '…'}）</summary><ul className="mt-2 space-y-2 text-xs text-zinc-400">{diff.data?.map(item => <li key={`${item.role_id}:${item.subtask_id}:${item.field}`}>{taskLabels[item.subtask_id] ?? item.subtask_id} · {item.field}<pre className="max-h-28 overflow-auto whitespace-pre-wrap">{JSON.stringify(item.before)} → {JSON.stringify(item.after)}</pre></li>)}</ul></details><Button disabled={blocked} onClick={() => setAction('activate')}>启用已保存草稿</Button><div className="flex flex-wrap gap-2">{versions.data?.map(version => <Button key={version.active_revision} size="sm" variant="outline" disabled={blocked || version.active_revision === overview.active?.active_revision} onClick={() => setAction(version.active_revision)}>回滚至 v{version.active_revision}</Button>)}</div><Dialog open={action !== null} onOpenChange={open => { if (!open && !pending) setAction(null); }}><DialogContent><DialogTitle>{action === 'activate' ? '启用草稿？' : `回滚到版本 ${action}？`}</DialogTitle><DialogDescription>作用范围：当前项目之后创建的任务。现有任务、生成内容和执行快照保持不变。服务端将重新校验草稿与启用版本，防止覆盖其他人的修改。</DialogDescription><DialogFooter><Button variant="outline" disabled={pending} onClick={() => setAction(null)}>取消</Button><Button disabled={blocked} onClick={() => void submit()}>{pending ? '正在提交…' : '确认'}</Button></DialogFooter></DialogContent></Dialog></section>;
}
