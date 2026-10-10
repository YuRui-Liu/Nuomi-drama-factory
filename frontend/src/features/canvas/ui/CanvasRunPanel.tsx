import { useCanvasStore } from '@/stores/canvasStore';
import { useCanvasRun } from '../application/useCanvasRun';

const statuses: Record<string, string> = { pending: '等待执行', running: '制作中', succeeded: '已完成',
  failed: '执行失败', blocked: '等待处理', submission_unknown: '提交结果待核实', cancel_requested: '正在停止', cancelled: '已停止' };

export function CanvasRunPanel({ project, canvas, enabled }: { project: string; canvas: string; enabled: boolean }) {
  const selected = useCanvasStore(s => s.selectedNodeId);
  const hasFinal = useCanvasStore(s => s.nodes.some(n => n.id === 'final'));
  const { run, error, busy, execute, control } = useCanvasRun(project, canvas, enabled);
  const target = selected ?? (hasFinal ? 'final' : null);
  const active = run && ['pending', 'running', 'cancel_requested', 'submission_unknown'].includes(run.status);
  const attempts = run ? Object.values(run.attempts).map(list => list[list.length - 1]) : [];
  const succeeded = attempts.filter(a => a?.status === 'succeeded').length;
  const failure = attempts.find(a => a?.error)?.error;
  return <div className="absolute bottom-4 left-4 z-20 max-w-sm rounded-xl border border-white/15 bg-[#17191c]/95 p-3 text-xs text-white shadow-xl">
    <div className="mb-2 flex items-center gap-3">
      <span>画布制作</span>
      {run && <span className="text-white/65">{statuses[run.status] ?? run.status} · {succeeded}/{attempts.length}</span>}
    </div>
    <div className="flex gap-2">
      <button className="rounded bg-lime-300 px-3 py-1.5 text-black disabled:opacity-40" disabled={!enabled || !target || busy || !!active}
        onClick={() => target && void execute(target)}>{selected ? '运行选中节点及上游' : '制作完整一集'}</button>
      {run && ['blocked', 'submission_unknown'].includes(run.status) && <button disabled={busy} onClick={() => void control('resume')}>恢复检查</button>}
      {run?.status === 'failed' && <button disabled={busy} onClick={() => void control('retry')}>重试失败节点</button>}
      {active && <button disabled={busy} onClick={() => void control('cancel')}>停止</button>}
    </div>
    {Boolean(error || failure) && <p role="alert" className="mt-2 max-h-24 overflow-auto text-red-300">{error || (typeof failure === 'string' ? failure : JSON.stringify(failure))}</p>}
  </div>;
}
