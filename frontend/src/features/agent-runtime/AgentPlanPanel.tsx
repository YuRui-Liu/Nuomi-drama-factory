import { useState } from 'react';
import { confirmAgentPlan, editAgentPlan, previewAgentPlan, type AgentPlan } from '@/api/agentPlans';
import type { AgentDiscussionScope } from '@/api/agentDiscussions';

type Owner = { project: string; canvas: string; conversation: string };
type Node = { id: string; nodeType?: string; title?: string };

function requestId(prefix: string) {
  return `${prefix}-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`}`;
}

export function AgentPlanPanel({ owner, nodes }: { owner: Owner; nodes: readonly Node[] }) {
  const [message, setMessage] = useState('');
  const [plan, setPlan] = useState<AgentPlan | null>(null);
  const [dirty, setDirty] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const scope: AgentDiscussionScope = { canvas_id: owner.canvas, conversation_id: owner.conversation };
  const selection = nodes.length
    ? { kind: 'nodes' as const, node_ids: nodes.map((node) => node.id) }
    : { kind: 'canvas' as const };

  async function preview() {
    if (!message.trim()) return;
    setWorking(true);
    setError('');
    setNotice('');
    try {
      const next = await previewAgentPlan(owner.project, {
        request_id: requestId('agent-plan'), ...scope, message: message.trim(), selection,
      });
      setPlan(next);
      setDirty(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '计划预览失败');
    } finally {
      setWorking(false);
    }
  }

  async function saveEdit() {
    if (!plan || !dirty) return;
    setWorking(true);
    setError('');
    setNotice('');
    try {
      const next = await editAgentPlan(owner.project, plan.id, scope, {
        request_id: requestId('agent-plan-edit'), expected_revision: plan.revision,
        tasks: plan.tasks,
      });
      setPlan(next);
      setDirty(false);
      setNotice('计划修改已保存');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '计划修改失败');
    } finally {
      setWorking(false);
    }
  }

  async function confirmText() {
    if (!plan || dirty || plan.required_permission !== 'text') return;
    setWorking(true);
    setError('');
    setNotice('');
    try {
      const next = await confirmAgentPlan(owner.project, plan.id, scope, {
        request_id: requestId('agent-plan-confirm'), expected_revision: plan.revision,
        confirmed: true, permission: 'text',
      });
      setPlan(next);
      setNotice(next.run_id ? `已启动文本任务 ${next.run_id}` : '文本计划已确认');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '确认计划失败');
    } finally {
      setWorking(false);
    }
  }

  function updateTask(taskKey: string, update: (task: AgentPlan['tasks'][number]) => AgentPlan['tasks'][number]) {
    setPlan((current) => current ? {
      ...current, tasks: current.tasks.map((task) => task.task_key === taskKey ? update(task) : task),
    } : current);
    setDirty(true);
  }

  function addTask() {
    if (!plan || !plan.tasks[0]) return;
    const taskKey = `task-${plan.tasks.length + 1}`;
    setPlan({ ...plan, tasks: [...plan.tasks, {
      ...plan.tasks[0], task_key: taskKey, goal: '', acceptance: ['完成此任务的文本产出。'], depends_on: [],
    }] });
    setDirty(true);
  }

  function removeTask(taskKey: string) {
    if (!plan || plan.tasks.length <= 1) return;
    setPlan({ ...plan, tasks: plan.tasks.filter((task) => task.task_key !== taskKey)
      .map((task) => ({ ...task, depends_on: task.depends_on.filter((dependency) => dependency !== taskKey) })) });
    setDirty(true);
  }

  return (
    <section className="space-y-2 rounded-md border border-white/10 p-3" aria-label="Agent 计划">
      <h3 className="font-medium">Agent 任务计划</h3>
      <label className="block space-y-1">
        <span>任务描述</span>
        <textarea aria-label="Agent 任务描述" value={message} onChange={(event) => setMessage(event.target.value)}
          rows={2} className="w-full rounded border border-white/15 bg-background p-2" />
      </label>
      <p className="text-muted-foreground">项目 {owner.project} · 画布 {owner.canvas} · 范围：
        {nodes.length ? nodes.map((node) => node.title || node.id).join('、') : '整张画布'}</p>
      <button type="button" onClick={() => void preview()} disabled={working || !message.trim()}
        className="rounded border border-white/15 px-3 py-1.5 disabled:opacity-50">预览计划</button>
      {error && <p role="alert" className="text-destructive">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {plan && <div className="space-y-2 border-t border-white/10 pt-2">
        <p>冻结输入：{plan.frozen_inputs.map((input) =>
          `${input.node_id} · 画布版本 ${input.canvas_revision} · ${input.input_fingerprint}`,
        ).join('；') || '无节点输入'}</p>
        <p>依赖顺序计划草稿：确认后由单个 coordinator 文本任务执行，不会并行启动各节点。</p>
        <div className="space-y-2">
          {plan.tasks.map((task) => <fieldset key={task.task_key} className="space-y-1 rounded border border-white/10 p-2">
            <legend className="px-1">任务 {task.task_key}</legend>
            <p>职责：{task.role.label}（{task.role.adoption_id} · r{task.role.revision}）</p>
            <p>Skill：{task.skills.map((skill) => skill.id).join('、') || '无'}</p>
            <label className="block space-y-1">
              <span>任务目标 {task.task_key}</span>
              <textarea aria-label={`任务目标 ${task.task_key}`} value={task.goal}
                onChange={(event) => updateTask(task.task_key, (item) => ({ ...item, goal: event.target.value }))}
                rows={2} className="w-full rounded border border-white/15 bg-background p-2" />
            </label>
            <label className="block space-y-1">
              <span>依赖 {task.task_key}</span>
              <select aria-label={`依赖 ${task.task_key}`} multiple value={task.depends_on}
                onChange={(event) => updateTask(task.task_key, (item) => ({
                  ...item, depends_on: Array.from(event.target.selectedOptions, (option) => option.value),
                }))} className="w-full rounded border border-white/15 bg-background p-1">
                {plan.tasks.filter((candidate) => candidate.task_key !== task.task_key).map((candidate) =>
                  <option key={candidate.task_key} value={candidate.task_key}>{candidate.task_key}</option>)}
              </select>
            </label>
            <p>输出：{task.outputs.map((output) => `${output.type} → ${output.target}`).join('；')}</p>
            <p>权限：{task.permission === 'text' ? '文本候选' : task.permission}</p>
            {plan.tasks.length > 1 && <button type="button" onClick={() => removeTask(task.task_key)}
              className="rounded border border-white/15 px-2 py-1">删除任务 {task.task_key}</button>}
          </fieldset>)}
        </div>
        {plan.required_permission === 'text' ? <>
          <button type="button" onClick={addTask} disabled={working}
            className="mr-2 rounded border border-white/15 px-3 py-1.5 disabled:opacity-50">添加任务</button>
          <button type="button" onClick={() => void saveEdit()} disabled={working || !dirty}
            className="mr-2 rounded border border-white/15 px-3 py-1.5 disabled:opacity-50">保存编辑</button>
          <button type="button" onClick={() => void confirmText()} disabled={working || dirty || plan.state === 'confirmed'}
            className="rounded bg-primary px-3 py-1.5 text-primary-foreground disabled:opacity-50">确认文本执行</button>
        </> : <p role="status" className="text-amber-300">此任务需要单独的媒体授权，不能从文本计划入口启动。</p>}
      </div>}
    </section>
  );
}
