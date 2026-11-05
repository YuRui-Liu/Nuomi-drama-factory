import { useCanvasStore } from '@/stores/canvasStore';
import { useCanvasRun } from '../application/useCanvasRun';
import { flushFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';

const statuses: Record<string, string> = { pending: '等待执行', running: '制作中', succeeded: '已完成',
  failed: '执行失败', blocked: '等待处理', submission_unknown: '提交结果待核实', cancel_requested: '正在停止',
  cancelled: '已停止', submitting: '正在提交', verifying: '正在核验' };

function describeError(error: unknown): string {
  if (typeof error === 'string') return error;
  if (!error || typeof error !== 'object') return '';
  const detail = error as Record<string, unknown>;
  if (typeof detail.message === 'string') return detail.message;
  if (typeof detail.reason === 'string') return detail.reason;
  return typeof detail.code === 'string' ? detail.code : '';
}

export function CanvasRunPanel({ project, canvas, enabled }: { project: string; canvas: string; enabled: boolean }) {
  const selected = useCanvasStore(s => s.selectedNodeId);
  const hasFinal = useCanvasStore(s => s.nodes.some(n => n.id === 'final'));
  const nodes = useCanvasStore(s => s.nodes);
  const { run, error, busy, execute, control } = useCanvasRun(project, canvas, enabled);
  const target = selected ?? (hasFinal ? 'final' : null);
  const active = run && ['pending', 'running', 'cancel_requested', 'submission_unknown'].includes(run.status);
  const attempts = run ? Object.values(run.attempts).map(list => list[list.length - 1]) : [];
  const succeeded = attempts.filter(a => a?.status === 'succeeded').length;
  const failure = attempts.find(a => a?.error)?.error;
  if (!target && !run && !error) return null;
  return <details className="absolute bottom-20 left-4 z-20 max-w-[min(24rem,calc(100%-2rem))] rounded-xl border border-white/10 bg-[#17191c]/95 text-xs text-white shadow-xl">
    <summary className="cursor-pointer px-3 py-2 text-white/70">{run ? `制作任务 · ${statuses[run.status] ?? run.status}` : '运行选中内容'}</summary>
    <div className="space-y-2 border-t border-white/10 p-3">
    <div className="mb-2 flex items-center gap-3">
      <span>画布制作</span>
      {selected && <button disabled={!enabled} onClick={async () => {
        if (await flushFreezoneCanvasRuntime(project, canvas) !== true) return;
        const node = useCanvasStore.getState().nodes.find(n => n.id === selected);
        if (node) window.dispatchEvent(new CustomEvent('nuomi:chat-reference', {detail: {id: node.id, label: String(node.data.displayName || node.type), kind: 'node', canvas}}));
      }}>引用到对话</button>}
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
    {run && <div className="mt-3 max-h-48 overflow-auto border-t border-white/10 pt-2">
      <p className="mb-1 text-white/60">节点执行明细</p>
      <ul aria-label="节点执行状态" className="space-y-2">
        {Object.entries(run.attempts).map(([nodeId, history]) => {
          const attempt = history[history.length - 1];
          if (!attempt) return null;
          const node = nodes.find(item => item.id === nodeId);
          const label = String(node?.data.displayName || nodeId);
          const reason = describeError(attempt.error);
          return <li key={nodeId} className="border-l border-white/15 pl-2">
            <p>{label} · {statuses[attempt.status] ?? attempt.status}</p>
            {reason && <p className="text-red-300">{reason}</p>}
          </li>;
        })}
      </ul>
    </div>}
    </div>
  </details>;
}
