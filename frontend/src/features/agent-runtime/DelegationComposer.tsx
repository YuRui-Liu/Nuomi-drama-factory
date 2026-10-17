import { useCallback, useEffect, useRef, useState } from 'react';
import { useReactFlow } from '@xyflow/react';
import { Button } from '@/components/ui/button';
import type { useAgentRuntime } from './useAgentRuntime';

type Runtime = ReturnType<typeof useAgentRuntime>;

const operationNames: Record<string, string> = {
  generate: '生成',
  edit: '编辑',
  compose: '合成',
};

function operationLabel(operation: { name: string; title?: string | null; label?: string | null }) {
  return operation.title?.trim() || operation.label?.trim() || operationNames[operation.name] || operation.name;
}

function shortNodeId(id: string) {
  return id.length <= 8 ? id : id.slice(-8);
}

export function DelegationComposer({
  runtime: r,
  goal,
  onGoalChange,
  onSubmit,
  onClose,
  onOpenCrew,
  showGoal = true,
  showAction = true,
  embedded = false,
}: {
  runtime: Runtime;
  goal: string;
  onGoalChange: (value: string) => void;
  onSubmit?: (goal: string) => Promise<boolean> | void;
  onClose?: () => void;
  onOpenCrew?: () => void;
  showGoal?: boolean;
  showAction?: boolean;
  embedded?: boolean;
}) {
  const delegatedProfile = r.delegateAgentId === 'main'
    ? r.main?.current_agent?.effective
    : r.adoptions.find((item) => item.adoption_id === r.delegateAgentId)?.effective;
  const hasRunGrant = Object.values(r.nodeOperationGrants).some((operations) =>
    operations.some((operation) => operation !== 'patch'),
  );
  const eligibleExperts = r.delegateAgentId === 'main' ? r.adoptions : [];
  const intentPending = r.intent?.phase === 'pending';
  const canDelegate = r.canSend && !!goal.trim() && !!r.nodes.length && !r.missingNodes.length;
  const flow = useReactFlow();
  const contentRef = useRef<HTMLDivElement | null>(null);
  const [showScrollHint, setShowScrollHint] = useState(false);
  const updateScrollHint = useCallback(() => {
    const content = contentRef.current;
    if (!content) return;
    const overflows = content.scrollHeight > content.clientHeight + 1;
    const atBottom = content.scrollTop + content.clientHeight >= content.scrollHeight - 4;
    setShowScrollHint(overflows && !atBottom);
  }, []);

  useEffect(() => {
    const content = contentRef.current;
    if (!content) return;
    updateScrollHint();
    content.addEventListener('scroll', updateScrollHint, { passive: true });
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(updateScrollHint);
    observer?.observe(content);
    return () => {
      content.removeEventListener('scroll', updateScrollHint);
      observer?.disconnect();
    };
  }, [
    goal,
    r.adoptions,
    r.builtins,
    r.canvasSyncNotice,
    r.directoryNotice,
    r.error,
    r.intent,
    r.limit,
    r.main,
    r.missingNodes,
    r.nodeCapabilities,
    r.nodeOperationGrants,
    r.nodes,
    r.personal,
    r.runtime,
    r.selectionChanged,
    r.specialists,
    showGoal,
    updateScrollHint,
  ]);

  return (
    <section className="flex min-h-0 flex-1 flex-col">
      <div ref={contentRef} data-testid="delegation-config-scroll" className="flex min-h-0 flex-1 flex-col overflow-y-auto px-4 py-3 text-sm">
        {showGoal && (
          <label className="grid gap-2">
            <span className="font-medium">本次委托目标</span>
            <textarea
              aria-label="本次委托目标"
              autoFocus={!embedded}
              value={goal}
              onChange={(event) => onGoalChange(event.target.value)}
              placeholder="描述要为选中节点完成的工作"
              rows={3}
              maxLength={12000}
              className="min-h-20 resize-y rounded-xl border border-white/10 bg-black/20 px-3 py-2.5 text-sm leading-6 outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-primary"
            />
          </label>
        )}

        <section aria-label="目标范围" className="mt-4 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <p className="font-medium">目标范围 · {r.nodes.length} 个节点</p>
            {r.selectionChanged && (
              <Button size="sm" variant="outline" onClick={r.captureSelection} disabled={r.working}>
                更新为当前选择
              </Button>
            )}
          </div>
          {!r.nodes.length && <p className="text-xs text-muted-foreground">请选择画布节点后再发起委托。</p>}
          {r.nodes.map((node) => (
            <div key={node.id} className="flex min-w-0 items-center gap-2 text-xs">
              <button
                type="button"
                aria-label={`定位节点 ${node.title}，ID ${shortNodeId(node.id)}`}
                title={`定位节点 ${node.title} · ID ${node.id}`}
                disabled={r.missingNodes.includes(node.id)}
                className="flex min-h-9 min-w-0 flex-1 items-center gap-1 truncate text-left underline decoration-dotted underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-50"
                onClick={() => void flow.fitView({ nodes: [{ id: node.id }], padding: 0.5, duration: 250, maxZoom: 1 })}
              >
                <span className="truncate">{node.title}</span>
                <span aria-hidden="true" className="shrink-0 text-[10px] text-muted-foreground">· {shortNodeId(node.id)}</span>
              </button>
              {r.missingNodes.includes(node.id) && <span className="text-amber-300">已失效</span>}
              <button
                type="button"
                aria-label={`移除 ${node.title}`}
                className="min-h-9 rounded px-2 text-muted-foreground underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                onClick={() => r.setNodes((nodes) => nodes.filter((item) => item.id !== node.id))}
              >
                移除
              </button>
            </div>
          ))}
          {r.selectionChanged && !embedded && (
            <p role="status" className="text-xs text-amber-300">
              当前范围保持冻结；更新后会清除已有授权。
            </p>
          )}
          {!!r.missingNodes.length && (
            <p role="alert" className="text-xs text-amber-300">
              固定范围中的节点已删除或无法访问：{r.missingNodes.join('、')}。移除失效节点或更新范围后才能委托。
            </p>
          )}
        </section>

        <section aria-label="委托负责人" className="mt-4 space-y-2 border-t border-white/10 pt-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <label className="flex min-w-0 flex-1 items-center gap-2 text-xs">
              <span className="shrink-0">负责人</span>
              <select
                aria-label="本次委托成员"
                value={r.delegateAgentId}
                onChange={(event) => r.selectDelegateAgent(event.target.value)}
                disabled={r.working || intentPending}
                className="min-h-10 min-w-0 flex-1 rounded-md border border-white/10 bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
              >
                <option value="main" disabled={!r.main?.current_agent}>
                  项目主创 · {r.main?.current_agent?.effective.name || '尚未选择'}
                </option>
                {r.adoptions.map((adoption) => (
                  <option key={adoption.adoption_id} value={adoption.adoption_id}>
                    {adoption.effective.name} · v{adoption.revision}
                  </option>
                ))}
              </select>
            </label>
            {onOpenCrew && (
              <Button type="button" variant="ghost" size="sm" onClick={onOpenCrew}>
                管理项目机组
              </Button>
            )}
          </div>
          {eligibleExperts.length > 0 && (
            <details className="text-xs">
              <summary className="cursor-pointer py-1 font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
                候选专家 · {r.specialists.length} 位已选
              </summary>
              <div className="grid gap-1 py-2">
                {eligibleExperts.map((adoption) => (
                  <label key={adoption.adoption_id} className="flex min-h-9 items-center gap-2">
                    <input
                      type="checkbox"
                      checked={r.specialists.includes(adoption.adoption_id)}
                      disabled={r.working || intentPending}
                      onChange={(event) => r.setSpecialists((values) => event.target.checked
                        ? [...values, adoption.adoption_id]
                        : values.filter((value) => value !== adoption.adoption_id))}
                      className="size-4 accent-primary"
                    />
                    {adoption.effective.name}
                  </label>
                ))}
              </div>
            </details>
          )}
        </section>

        {r.nodes.length > 0 && (
          <section aria-label="节点授权" className="mt-4 space-y-3 border-t border-white/10 pt-3">
            <div>
              <p className="font-medium">节点操作授权</p>
              <p className="mt-1 text-xs text-muted-foreground">默认仅准备方案；修改和具体运行分别确认。</p>
            </div>
            {r.nodes.map((node) => {
              const capability = r.nodeCapabilities.find((item) => item.node_type === node.nodeType);
              const granted = r.nodeOperationGrants[node.id] ?? [];
              const canPatch = !!delegatedProfile?.capabilities.includes('node.patch');
              const canRun = !!delegatedProfile?.capabilities.includes('node.run');
              return (
                <div key={node.id} className="space-y-2 border-b border-white/10 pb-3 last:border-0">
                  <p className="truncate text-xs font-medium">{node.title}</p>
                  <label className="flex min-h-9 items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      checked={granted.includes('patch')}
                      disabled={!canPatch || r.working || intentPending || r.missingNodes.includes(node.id)}
                      onChange={(event) => r.setNodeOperationGrant(node.id, 'patch', event.target.checked)}
                      className="size-4 accent-primary"
                    />
                    允许修改此节点字段
                  </label>
                  {!canPatch && <p className="pl-6 text-xs text-muted-foreground">当前成员不支持修改节点</p>}
                  {canRun && capability?.operations.map((operation) => (
                    <label key={operation.name} className="flex min-h-9 items-center gap-2 text-xs">
                      <input
                        type="checkbox"
                        checked={granted.includes(operation.name)}
                        disabled={!operation.available || r.working || intentPending || r.missingNodes.includes(node.id)}
                        onChange={(event) => r.setNodeOperationGrant(node.id, operation.name, event.target.checked)}
                        className="size-4 accent-primary"
                      />
                      <span>允许运行 {operationLabel(operation)}</span>
                      {!operation.available && (
                        <span className="text-muted-foreground">暂不可用 · {operation.reason ?? '节点适配器未就绪'}</span>
                      )}
                    </label>
                  ))}
                  {!canRun && <p className="pl-6 text-xs text-muted-foreground">当前成员不支持运行节点</p>}
                  {canRun && !capability && (
                    <p className="pl-6 text-xs text-muted-foreground">尚未读取此节点的实际操作目录，暂不能授权运行。</p>
                  )}
                </div>
              );
            })}
            {hasRunGrant && (
              <label className="flex min-h-10 items-center justify-between gap-3 text-xs">
                本次具体节点运行次数上限
                <input
                  aria-label="本次节点运行次数上限"
                  type="number"
                  min={1}
                  value={r.maxNodeRuns}
                  disabled={r.working || intentPending}
                  onChange={(event) => r.setMaxNodeRuns(Math.max(1, Number(event.target.value) || 1))}
                  className="w-20 rounded-md border border-white/10 bg-background px-2 py-1.5"
                />
              </label>
            )}
            {!r.nodeCapabilities.length && (
              <p role="status" className="text-xs text-amber-300">无法读取节点操作目录；当前仅可准备方案或授权修改。</p>
            )}
          </section>
        )}

        <details className="mt-4 border-t border-white/10 pt-3" onToggle={() => requestAnimationFrame(updateScrollHint)}>
          <summary className="cursor-pointer py-1 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
            高级设置 · 最多 {r.limit} 次模型调用
          </summary>
          <label className="mt-2 flex min-h-10 items-center justify-between gap-3 text-xs">
            模型调用上限
            <select
              aria-label="模型调用上限"
              className="rounded-md border border-white/10 bg-background px-2 py-1.5"
              value={r.limit}
              onChange={(event) => r.setLimit(Number(event.target.value))}
              disabled={r.working || intentPending}
            >
              {Array.from({ length: Math.min(r.runtime?.max_model_calls ?? 24, 24) }, (_, index) => index + 1).map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
          </label>
          <p className="text-xs text-muted-foreground">
            默认 {r.runtime?.default_model_calls ?? 8} 次 · 服务端上限 {r.runtime?.max_model_calls ?? 24} 次。
          </p>
        </details>

        {r.runtime && !r.runtime.available && (
          <p role="status" className="mt-3 text-xs text-amber-300">
            文本模型尚未配置或当前运行方式不可用；不能发起委托。
          </p>
        )}

        {r.directoryNotice && <p role="status" className="mt-3 text-xs text-amber-300">{r.directoryNotice}</p>}
        {r.canvasSyncNotice && <p role="status" className="mt-2 text-xs text-amber-300">{r.canvasSyncNotice}</p>}
        {r.error && <p role="alert" className="mt-3 text-xs text-destructive">{r.error}</p>}
        {r.intent && (
          <section className="mt-3 space-y-2 rounded-lg bg-white/[0.04] p-3 text-xs">
            <p>{r.intent.label}</p>
            <p>{r.intent.phase === 'pending' ? '请求结果尚待确认' : r.intent.phase === 'failed' ? '请求未完成，目标已保留' : '请求已保存'}</p>
            {r.intent.error && <p role="alert">{r.intent.error}</p>}
            {intentPending ? (
              <Button type="button" size="sm" disabled={r.working} onClick={() => void r.recover()}>恢复原请求</Button>
            ) : (
              <Button type="button" size="sm" disabled={r.working} onClick={() => void r.finish()}>保留记录，准备新任务</Button>
            )}
          </section>
        )}
      </div>
      {showAction && (
        <div className="flex shrink-0 items-center justify-between gap-2 border-t border-white/10 px-4 py-3">
          {onClose ? (
            <Button type="button" variant="ghost" onClick={onClose}>关闭</Button>
          ) : <span />}
          {showScrollHint && <span role="status" className="min-w-0 flex-1 truncate text-center text-[11px] text-muted-foreground">上方可滚动查看全部配置</span>}
          <Button
            type="button"
            onClick={() => {
              if (canDelegate && onSubmit && !r.working && !intentPending) void onSubmit(goal);
            }}
            disabled={!canDelegate || !onSubmit || r.working || intentPending}
          >
            发起委托
          </Button>
        </div>
      )}
    </section>
  );
}
