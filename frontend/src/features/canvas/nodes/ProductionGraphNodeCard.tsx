import type { ReactNode } from 'react';
import { Handle, Position } from '@xyflow/react';
import { Clapperboard } from 'lucide-react';
import { NodeHeader, NODE_HEADER_FLOATING_POSITION_CLASS } from '../ui/NodeHeader';

const orchestrationStatuses: Record<string, string> = {
  pending: '等待执行', running: '制作中', succeeded: '已完成', failed: '执行失败',
  blocked: '等待处理', submission_unknown: '提交结果待核实', stale: '结果已过期',
  source_changed: '来源已变化', cancelled: '已停止',
};

function errorText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (!value || typeof value !== 'object') return '';
  const error = value as Record<string, unknown>;
  if (typeof error.message === 'string') return error.message;
  if (typeof error.reason === 'string') return error.reason;
  return typeof error.code === 'string' ? error.code : '';
}

export function ProductionGraphNodeCard({
  title,
  summary,
  validationError,
  result,
  orchestration,
}: {
  title: string;
  summary: string;
  validationError?: string;
  result?: ReactNode;
  orchestration?: Record<string, unknown>;
}) {
  const status = typeof orchestration?.status === 'string' ? orchestration.status : '';
  const failure = errorText(orchestration?.error ?? orchestration?.message);

  return <div className="relative min-h-32 w-[360px] rounded-xl border border-border bg-card p-4 text-card-foreground shadow-sm">
    <NodeHeader className={NODE_HEADER_FLOATING_POSITION_CLASS} titleText={title} icon={<Clapperboard className="size-4" />} />
    <Handle type="target" position={Position.Left} id="target" />
    <Handle type="source" position={Position.Right} id="source" />
    <p className="mb-2 line-clamp-2 text-xs text-muted-foreground">{summary}</p>
    {validationError && <p role="alert" className="mb-2 text-xs text-destructive">{validationError}</p>}
    {status && <p role="status" className="mb-2 text-xs text-muted-foreground">{orchestrationStatuses[status] ?? status}</p>}
    {failure && <p role="alert" className="mb-2 text-xs text-destructive">{failure}</p>}
    {result}
  </div>;
}
