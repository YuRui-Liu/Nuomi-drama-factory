import { useState } from 'react';
import { useReactFlow } from '@xyflow/react';
import { Button } from '@/components/ui/button';
import { canStopAgentRun } from './useAgentRuntime';
import type { useAgentRuntime } from './useAgentRuntime';
import type { Delegation } from '@/api/agentRuns';

type Runtime = ReturnType<typeof useAgentRuntime>;

const stateLabels: Record<string, string> = {
  queued: '等待执行',
  model_inflight: '正在准备方案',
  applying: '正在保存候选',
  delivered: '候选已交付',
  partial: '部分完成',
  blocked: '需要处理',
  stopped: '已停止后续操作',
  submission_unknown: '已提交调用结果未知',
};
const modalityLabels: Record<string, string> = {
  image: '图片',
  video: '视频',
  audio: '音频',
  '3d': '3D',
};

const parentId = (delegation: Delegation) => delegation.parent_id ?? delegation.contract.parent_id ?? null;
const delegationId = (delegation: Delegation) => delegation.id ?? delegation.delegation_id ?? '';

function tokens(rounds: { usage?: { input_tokens?: number; output_tokens?: number } }[], key: 'input_tokens' | 'output_tokens') {
  const values = rounds.map((round) => round.usage?.[key]);
  const known = values.filter((value): value is number => typeof value === 'number');
  return known.length === values.length && known.length
    ? String(known.reduce((a, b) => a + b, 0))
    : known.length
      ? `${known.reduce((a, b) => a + b, 0)}（其余未知）`
      : '未知';
}

export function AgentRunStatus({
  runtime: r,
  compact = true,
}: {
  runtime: Runtime;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(!compact);
  const flow = useReactFlow();
  const locate = (ids: string[]) => void flow.fitView({
    nodes: [...new Set(ids)].map((id) => ({ id })),
    padding: 0.5,
    duration: 250,
    maxZoom: 1,
  });
  const historyIds = new Set(r.history.map(({ delegation }) => delegationId(delegation)));
  const roots = r.history.filter(({ delegation }) => {
    const parent = parentId(delegation);
    return !parent || !historyIds.has(parent);
  });
  const runCount = r.history.reduce((count, item) => count + item.runs.length, 0);

  return (
    <section aria-label="Agent 运行状态" className="shrink-0 border-t border-white/10 px-3 py-2 text-xs">
      {r.error && <p role="alert" className="mb-2 text-destructive">{r.error}</p>}
      {r.canvasSyncNotice && <p role="status" className="mb-2 text-amber-300">{r.canvasSyncNotice}</p>}
      {r.intent?.phase === 'pending' && (
        <div role="status" className="mb-2 flex flex-wrap items-center gap-2 rounded-lg bg-amber-300/[0.08] px-3 py-2 text-amber-200">
          <span className="min-w-0 flex-1">{r.intent.label} · 请求结果尚待确认</span>
          <Button type="button" size="sm" variant="outline" disabled={r.working} onClick={() => void r.recover()}>
            恢复原请求
          </Button>
        </div>
      )}
      {r.liveRun && (
        <div className="flex min-h-10 items-center gap-2">
          <span aria-hidden="true" className="size-2 shrink-0 rounded-full bg-primary" />
          <span className="min-w-0 flex-1 truncate font-medium">
            {stateLabels[r.liveRun.state] ?? r.liveRun.state} · {r.liveRun.rounds.length}/{r.liveRun.max_model_calls} 次调用
          </span>
          <Button size="sm" variant="outline" disabled={r.working || r.intent?.phase === 'pending'} onClick={() => void r.control(r.liveRun!, 'stop')}>
            停止
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>查看详情</Button>
        </div>
      )}
      {(runCount > 0 || r.intent) && (
        <>
          <button
            type="button"
            aria-expanded={open}
            className="flex min-h-9 w-full items-center justify-between gap-2 rounded px-1 text-left font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            onClick={() => setOpen((value) => !value)}
          >
            <span>任务记录 · {runCount}</span>
            <span aria-hidden="true">{open ? '收起' : '展开'}</span>
          </button>
          {open && (
            <div className="max-h-[min(40vh,24rem)] space-y-3 overflow-y-auto pb-2">
              {roots.map(({ delegation: root }) => {
                const children = new Map<string, typeof r.history>();
                for (const item of r.history) {
                  const parent = parentId(item.delegation);
                  if (parent) children.set(parent, [...(children.get(parent) ?? []), item]);
                }
                const records: typeof r.history = [];
                const queue = [root];
                const visited = new Set<string>();
                while (queue.length) {
                  const delegation = queue.shift()!;
                  const id = delegationId(delegation);
                  if (!id || visited.has(id)) continue;
                  visited.add(id);
                  const item = r.history.find((entry) => delegationId(entry.delegation) === id);
                  if (item) records.push(item);
                  for (const child of children.get(id) ?? []) queue.push(child.delegation);
                }
                const targets = [...new Set(records.flatMap(({ delegation }) =>
                  delegation.contract.expanded_scope.nodes.map((node) => node.node_id),
                ))];
                const recordsById = new Map(records.map((item) => [delegationId(item.delegation), item.delegation]));
                const participants = new Set<string>();
                const addParticipant = (id?: string) => {
                  if (!id) return;
                  participants.add(recordsById.get(id)?.contract.agent.effective.name ?? `委托 ${id}（配置不可用）`);
                };
                const addFrozenParticipant = (id?: string, revision?: number) => {
                  if (id) participants.add(`成员 ${id}${revision === undefined ? '' : ` · v${revision}`}`);
                };
                for (const { delegation, runs } of records) {
                  if (runs.length) addParticipant(delegationId(delegation));
                  for (const run of runs) {
                    for (const round of run.rounds) {
                      addParticipant(round.delegation_id);
                      if (!round.delegation_id) addFrozenParticipant(round.member?.adoption_id ?? round.member_id, round.member?.revision);
                    }
                    for (const output of run.outputs) {
                      addParticipant(output.delegation_id);
                      if (!output.delegation_id) addFrozenParticipant(output.member?.adoption_id ?? output.member_id, output.member?.revision);
                    }
                  }
                }
                return (
                  <article key={delegationId(root)} role="region" aria-label="共享任务记录" className="space-y-2 rounded-lg bg-white/[0.04] p-3">
                    <p className="whitespace-pre-wrap font-medium">{root.contract.goal}</p>
                    <p className="text-muted-foreground">
                      实际参与成员：{participants.size ? [...participants].join('、') : '尚无成员运行记录'}
                    </p>
                    {!!targets.length && (
                      <button className="min-h-9 underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" onClick={() => locate(targets)}>
                        定位本次固定目标（{targets.length}）
                      </button>
                    )}
                    {records.map(({ delegation, runs }) => (
                      <div key={delegationId(delegation)} className="space-y-2 border-t border-white/10 pt-2">
                        {delegationId(delegation) !== delegationId(root) && (
                          <p className="font-medium">子委托 · {delegation.contract.agent.effective.name}</p>
                        )}
                        {runs.map((run) => (
                          <div key={run.id} className="space-y-2">
                            <p className="font-medium">{stateLabels[run.state] ?? run.state}</p>
                            {run.error && <p role="alert" className="text-destructive">{run.error.message}</p>}
                            {run.outputs.map((output, index) => {
                              const nodeId = output.node_id ?? output.projection?.node_id;
                              return (
                                <div key={index} className="space-y-1 rounded-md bg-black/10 p-2">
                                  {nodeId && <button className="min-h-9 underline" onClick={() => locate([nodeId])}>定位节点 {nodeId}</button>}
                                  {output.operation === 'node.patch' && (
                                    <>
                                      <p>节点字段已修改 · 版本 {output.after_revision ?? output.revision ?? '已记录'}</p>
                                      {(output.changes ?? []).map((change, changeIndex) => (
                                        <p key={changeIndex} className="whitespace-pre-wrap">
                                          {change.path.map(String).join('.')}：{JSON.stringify(change.before)} → {JSON.stringify(change.after)}
                                        </p>
                                      ))}
                                    </>
                                  )}
                                  {output.operation === 'node.run' && (
                                    <p className="text-muted-foreground">节点运行 {output.status ?? '状态未记录'} · 投影 {output.projection?.status ?? '未记录'}</p>
                                  )}
                                  {output.artifacts?.map((artifact, artifactIndex) => artifact.url ? (
                                    <a key={artifact.id ?? artifactIndex} href={artifact.url} target="_blank" rel="noreferrer" className="block min-h-9 py-2 underline">
                                      {artifact.title ?? artifact.id ?? '运行产物'}
                                    </a>
                                  ) : <p key={artifact.id ?? artifactIndex}>{artifact.title ?? artifact.id}</p>)}
                                  <p>{output.object?.payload.title || output.plan?.title || output.title || (output.definition_ref ? '创作对象定义草案' : output.operation === 'node.patch' || output.operation === 'node.run' ? '' : '准备结果')}</p>
                                  <p className="whitespace-pre-wrap">{output.object?.payload.text || output.plan?.intent || (output.definition_ref ? `${output.definition_ref.name} · 第 ${output.definition_ref.revision} 版草案` : '')}</p>
                                  {output.plan && <p className="text-muted-foreground">{modalityLabels[output.plan.modality] ?? '媒体'}方案 · 尚未生成</p>}
                                  {output.definition_ref && <p className="text-muted-foreground">仅保存定义草案，尚未启用。</p>}
                                </div>
                              );
                            })}
                            {run.unmet.map((item, index) => <p key={index} className="text-amber-300">尚未完成：{item}</p>)}
                            <p className="text-muted-foreground">
                              已记录调用 {run.rounds.length}/{run.max_model_calls} 次 · 输入 {tokens(run.rounds, 'input_tokens')} / 输出 {tokens(run.rounds, 'output_tokens')} tokens · 费用未知
                            </p>
                            {run.rounds.some((round) => round.usage?.accounting?.status !== 'recorded') && (
                              <p className="text-muted-foreground">计量尚未确认，未知用量不能视为零费用。</p>
                            )}
                            <div className="flex flex-wrap gap-2">
                              {canStopAgentRun(run) && (
                                <Button size="sm" variant="outline" disabled={r.working || r.intent?.phase === 'pending'} onClick={() => void r.control(run, 'stop')}>
                                  停止后续操作
                                </Button>
                              )}
                              {['stopped', 'blocked', 'queued', 'applying', 'model_inflight'].includes(run.state) && (
                                <Button size="sm" variant="outline" disabled={r.working || r.intent?.phase === 'pending'} onClick={() => void r.control(run, 'resume')}>
                                  检查并恢复原运行
                                </Button>
                              )}
                            </div>
                            {run.state === 'submission_unknown' && <p>提交状态未知；检查状态，不会重复发送原请求。已保存候选仍可查看。</p>}
                          </div>
                        ))}
                      </div>
                    ))}
                  </article>
                );
              })}
            </div>
          )}
        </>
      )}
    </section>
  );
}
