import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { activateTeam, getTeamDiff } from './agent-team/api';

export function DirectorActivation({ project, draftRevision, activeRevision, dirty, onActivated }: {
  project: string; draftRevision: number; activeRevision: number; dirty: boolean; onActivated: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const diff = useQuery({ queryKey: ['agent-team', project, 'diff', draftRevision, activeRevision], queryFn: async () => { const result = await getTeamDiff(project); if (!Array.isArray(result)) throw new Error('差异数据无效'); return result; } });
  const otherChanges = diff.data?.some(item => item.role_id !== 'director' || item.subtask_id !== 'director_plan');
  const blocked = dirty || pending || !diff.isSuccess || otherChanges || !diff.data?.length;
  async function activate() {
    if (blocked) return;
    setPending(true); setError('');
    try { await activateTeam(project, draftRevision, activeRevision); await onActivated(); setOpen(false); }
    catch (e) { setError(e instanceof Error ? e.message : '启用失败，请刷新后重试'); }
    finally { setPending(false); }
  }
  return <section className="space-y-2 rounded-lg border border-primary/30 bg-primary/5 p-3" aria-label="导演方法生效状态">
    <p className="text-sm">已保存：草稿 v{draftRevision} · 实际生效：{activeRevision ? `团队 v${activeRevision}` : '内置默认方法'}</p>
    <p className="text-xs text-muted-foreground">{dirty ? '当前编辑尚未保存，请先保存配置，再比较并启用。' : diff.data?.length ? '草稿有待启用变化，AI 仍使用当前生效版本。' : diff.isSuccess ? '已保存方法与当前生效配置一致。' : '正在检查生效配置…'}</p>
    <Button variant="outline" disabled={dirty || pending || !diff.isSuccess || !diff.data?.length} onClick={() => setOpen(true)}>比较并启用导演方法</Button>
    {diff.isError && <p role="alert">无法读取差异。<Button variant="ghost" onClick={() => void diff.refetch()}>重试</Button></p>}
    <Dialog open={open} onOpenChange={value => { if (!pending) setOpen(value); }}><DialogContent className="max-h-[80vh] overflow-y-auto"><DialogTitle>比较导演方法并启用</DialogTitle><DialogDescription>启用影响当前项目之后创建的任务。已有方案和视频不自动重做；生成的新方案仍需审阅。</DialogDescription>
      {diff.data?.map(item => <div key={`${item.role_id}:${item.subtask_id}:${item.field}`} className="space-y-1 rounded border p-2 text-sm"><p>{item.role_id === 'director' ? '分镜导演' : item.role_id} · {item.field === 'director_preferences' ? '导演偏好' : item.field}</p><p className="text-xs text-muted-foreground">当前生效 → 已保存草稿</p><pre className="max-h-48 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(item.before, null, 2)}{'\n→\n'}{JSON.stringify(item.after, null, 2)}</pre></div>)}
      {otherChanges && <p role="alert">草稿还包含其他角色或子任务的修改。请前往<a className="underline" href={`/projects/${encodeURIComponent(project)}/studios?studio=agent-team`}>创作团队</a>统一比较并启用。</p>}
      {error && <p role="alert">{error}</p>}<Button disabled={Boolean(blocked)} onClick={() => void activate()}>{pending ? '正在启用…' : '确认启用已保存导演方法'}</Button>
    </DialogContent></Dialog>
  </section>;
}
