import { useState } from 'react';
import { useReactFlow } from '@xyflow/react';
import { Button } from '@/components/ui/button';
import type { useAgentRuntime } from './useAgentRuntime';
const labels: Record<string, string> = {
  queued: '等待执行',
  model_inflight: '正在准备方案',
  applying: '正在保存候选',
  delivered: '候选已交付',
  partial: '部分完成',
  blocked: '需要处理',
  stopped: '已停止后续操作',
  submission_unknown: '已提交调用结果未知',
};
const tokens = (
  rounds: { usage?: { input_tokens?: number; output_tokens?: number } }[],
  key: 'input_tokens' | 'output_tokens',
) => {
  const values = rounds.map((r) => r.usage?.[key]);
  const known = values.filter((v): v is number => typeof v === 'number');
  return known.length === values.length && known.length
    ? String(known.reduce((a, b) => a + b, 0))
    : known.length
      ? `${known.reduce((a, b) => a + b, 0)}（其余未知）`
      : '未知';
};
export function AgentRuntimePanel({
  runtime: r,
}: {
  runtime: ReturnType<typeof useAgentRuntime>;
}) {
  const [choice, setChoice] = useState('');
  const flow = useReactFlow();
  const locate = (ids: string[]) =>
    void flow.fitView({
      nodes: ids.map((id) => ({ id })),
      padding: 0.5,
      duration: 250,
      maxZoom: 1,
    });
  return (
    <div className="max-h-[35vh] shrink-0 space-y-2 overflow-y-auto border-b border-white/10 p-3 text-xs">
      <div className="space-y-1">
        <p className="font-medium">
          项目主创：{r.main?.current_agent?.effective.name || '尚未选择'}
        </p>
        {r.main?.current_agent && (
          <p className="text-muted-foreground">
            {r.adoptions.find((a) => a.adoption_id === r.main?.adoption_id)
              ?.source.kind === 'builtin'
              ? '来自内置 Agent'
              : '来自个人 Agent'}{' '}
            · 项目修订 {r.main.current_agent.revision}
          </p>
        )}
        <details className="space-y-2">
          <summary className="cursor-pointer text-muted-foreground">
            更换项目主创
          </summary>
          <select
            aria-label="选择项目主创"
            value={choice}
            onChange={(e) => setChoice(e.target.value)}
            className="w-full rounded border border-border bg-background p-1.5"
            disabled={r.working || r.intent?.phase === 'pending'}
          >
            <option value="">选择项目配置或明确采用一个版本</option>
            <optgroup label="项目已采用">
              {r.adoptions.map((a) => (
                <option key={a.adoption_id} value={`project:${a.adoption_id}`}>
                  {a.effective.name} · 项目修订 {a.revision}
                </option>
              ))}
            </optgroup>
            <optgroup label="内置 Agent">
              {r.builtins.map((a) => (
                <option
                  key={a.source.ref.id}
                  value={`builtin:${a.source.ref.id}`}
                >
                  {a.configuration.name} · 内置 v{a.source.ref.revision}
                </option>
              ))}
            </optgroup>
            <optgroup label="个人固定版本">
              {r.personal.map((a) => (
                <option key={a.id} value={`personal:${a.id}`}>
                  {a.configuration.name} · 个人 v{a.revision}
                </option>
              ))}
            </optgroup>
          </select>
          <Button
            size="sm"
            variant="outline"
            disabled={!choice || r.working || r.intent?.phase === 'pending'}
            onClick={() => void r.selectMain(choice)}
          >
            设为项目主创
          </Button>
          <p className="text-muted-foreground">
            配置不启动运行。发送只在明确范围内准备候选，不覆盖原内容。
          </p>
        </details>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={r.captureSelection}
          disabled={r.working}
        >
          引用当前选中节点
        </Button>
        <label>
          <input
            type="checkbox"
            checked={r.canvasScope}
            onChange={(e) => r.setCanvasScope(e.target.checked)}
            disabled={r.working}
          />{' '}
          当前画布准备范围
        </label>
      </div>
      {r.nodes.map((n) => (
        <div key={n.id} className="flex items-center gap-2">
          <button
            className="min-w-0 flex-1 truncate text-left underline"
            onClick={() => locate([n.id])}
          >
            {n.title}
          </button>
          <button
            aria-label={`移除 ${n.title}`}
            onClick={() => r.setNodes((v) => v.filter((x) => x.id !== n.id))}
          >
            移除
          </button>
        </div>
      ))}
      {!r.nodes.length && !r.canvasScope && (
        <p className="text-muted-foreground">
          尚未选择目标；不会默认授权整个画布。
        </p>
      )}
      <details className="space-y-2">
        <summary className="cursor-pointer text-muted-foreground">
          运行设置 · 最多 {r.limit} 次调用
        </summary>
        <label className="flex items-center justify-between">
          本次模型调用上限
          <select
            aria-label="模型调用上限"
            className="rounded border bg-background p-1"
            value={r.limit}
            onChange={(e) => r.setLimit(Number(e.target.value))}
            disabled={r.working}
          >
            {[1, 2, 3].map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </label>
        <p className="text-muted-foreground">
          主创与专业委派共用次数；费用暂无法确定。
        </p>
        <details>
          <summary>可选专业 Agent</summary>
          {r.adoptions.map((a) => (
            <label key={a.adoption_id} className="block py-1">
              <input
                type="checkbox"
                checked={r.specialists.includes(a.adoption_id)}
                onChange={(e) =>
                  r.setSpecialists((v) =>
                    e.target.checked
                      ? [...v, a.adoption_id]
                      : v.filter((id) => id !== a.adoption_id),
                  )
                }
              />
              {a.effective.name}
            </label>
          ))}
        </details>
      </details>
      {r.runtime && !r.runtime.available && (
        <p role="status" className="text-amber-300">
          文本模型尚未配置或当前运行方式不可用；不能发送，不会切换到旧工具。
        </p>
      )}
      {r.error && (
        <p role="alert" className="text-destructive">
          {r.error}
        </p>
      )}
      {r.intent && (
        <section className="space-y-2 rounded border border-border p-2">
          <p>{r.intent.label}</p>
          <p>
            {r.intent.phase === 'pending'
              ? '请求结果尚待确认'
              : r.intent.phase === 'failed'
                ? '请求未完成，原草稿已保留'
                : '请求已保存'}
          </p>
          {r.intent.error && <p role="alert">{r.intent.error}</p>}
          {r.intent.phase === 'pending' ? (
            <Button
              size="sm"
              disabled={r.working}
              onClick={() => void r.recover()}
            >
              恢复原请求
            </Button>
          ) : (
            <Button
              size="sm"
              disabled={r.working}
              onClick={() => void r.finish()}
            >
              保留记录，准备新任务
            </Button>
          )}
        </section>
      )}
      {r.history.map(({ delegation: d, runs }) => (
        <section
          key={d.id || d.delegation_id}
          className="space-y-2 rounded border border-white/10 p-2"
        >
          <p className="whitespace-pre-wrap font-medium">{d.contract.goal}</p>
          <p className="text-muted-foreground">
            {d.contract.agent.effective.name}
          </p>
          <button
            className="underline"
            onClick={() =>
              locate(d.contract.expanded_scope.nodes.map((n) => n.node_id))
            }
          >
            定位本次固定目标（{d.contract.expanded_scope.nodes.length}）
          </button>
          {runs.map((run) => (
            <div key={run.id} className="space-y-2">
              <p>{labels[run.state] || '正在读取状态'}</p>
              {run.error && <p role="alert">{run.error.message}</p>}
              {run.outputs.map((output, i) => (
                <article key={i} className="rounded bg-white/5 p-2">
                  <p>
                    {output.object?.payload.title ||
                      output.plan?.title ||
                      output.title ||
                      (output.definition_ref ? '创作对象定义草案' : '准备结果')}
                  </p>
                  <p className="whitespace-pre-wrap">
                    {output.object?.payload.text ||
                      output.plan?.intent ||
                      (output.definition_ref
                        ? `${output.definition_ref.name} · 第 ${output.definition_ref.revision} 版草案`
                        : '结果已保存')}
                  </p>
                  {output.plan && (
                    <p className="text-muted-foreground">
                      {
                        {
                          image: '图片',
                          video: '视频',
                          audio: '音频',
                          '3d': '3D',
                        }[output.plan.modality]
                      }
                      方案 · 尚未生成
                    </p>
                  )}
                  {output.definition_ref && (
                    <p className="text-muted-foreground">
                      仅保存定义草案，尚未启用。
                    </p>
                  )}
                </article>
              ))}
              {run.unmet.map((item, i) => (
                <p key={i} className="text-amber-300">
                  尚未完成：{item}
                </p>
              ))}
              <p className="text-muted-foreground">
                已记录调用 {run.rounds.length}/{run.max_model_calls} 次 · 输入{' '}
                {tokens(run.rounds, 'input_tokens')} / 输出{' '}
                {tokens(run.rounds, 'output_tokens')} tokens · 费用未知
              </p>
              {run.rounds.some(
                (x) => x.usage?.accounting?.status !== 'recorded',
              ) && (
                <p className="text-muted-foreground">
                  计量尚未确认，未知用量不能视为零费用。
                </p>
              )}
              {['queued', 'model_inflight', 'applying'].includes(run.state) && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={r.working || r.intent?.phase === 'pending'}
                  onClick={() => void r.control(run, 'stop')}
                >
                  停止后续操作
                </Button>
              )}
              {[
                'stopped',
                'blocked',
                'queued',
                'applying',
                'model_inflight',
              ].includes(run.state) && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={r.working || r.intent?.phase === 'pending'}
                  onClick={() => void r.control(run, 'resume')}
                >
                  检查并恢复原运行
                </Button>
              )}
              {run.state === 'submission_unknown' && (
                <p>已发送调用结果未知，不会盲目重发。已保存候选仍可查看。</p>
              )}
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
