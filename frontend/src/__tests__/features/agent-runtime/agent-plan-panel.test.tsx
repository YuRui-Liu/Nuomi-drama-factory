import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { AgentPlanPanel } from '@/features/agent-runtime/AgentPlanPanel';
import * as plans from '@/api/agentPlans';

vi.mock('@/api/agentPlans', () => ({
  previewAgentPlan: vi.fn(),
  editAgentPlan: vi.fn(),
  confirmAgentPlan: vi.fn(),
}));

const owner = { user: 'alice', project: 'p', canvas: 'canvas-1', conversation: 'conversation-1' };
const runtimeNodes = [{ id: 'node-1', nodeType: 'textAnnotationNode', title: '开场段落' }];
const plan = {
  id: 'plan-1', revision: 1, state: 'draft', message: '整理开场候选',
  scope: { kind: 'nodes', canvas_id: 'canvas-1', node_ids: ['node-1'], observed_canvas_revision: 4 },
  frozen_inputs: [{ kind: 'canvas_node', canvas_id: 'canvas-1', node_id: 'node-1',
    canvas_revision: 4, input_fingerprint: 'fingerprint-1' }],
  tasks: [{ task_key: 'main', goal: '整理开场候选', acceptance: ['形成一项候选'],
    role: { adoption_id: 'lead-1', revision: 2, label: '项目主创' },
    skills: [{ id: 'script-master', documents: [{ path: 'skills/script-master/SKILL.md', sha256: 'abc' }] }],
    depends_on: [], outputs: [{ type: 'candidate', target: 'canvas' }], permission: 'text', operation: 'proposal.create' }],
  required_permission: 'text',
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(plans.previewAgentPlan).mockResolvedValue(plan as never);
  vi.mocked(plans.editAgentPlan).mockResolvedValue({ ...plan, revision: 2,
    tasks: [{ ...plan.tasks[0], goal: '改写后的目标' }] } as never);
  vi.mocked(plans.confirmAgentPlan).mockResolvedValue({ ...plan, state: 'confirmed', run_id: 'run-1' } as never);
});

it('previews the selected scope, displays the editable task contract, and confirms text work separately', async () => {
  render(<AgentPlanPanel owner={owner} nodes={runtimeNodes} />);
  fireEvent.change(screen.getByLabelText('Agent 任务描述'), { target: { value: '整理开场候选' } });
  fireEvent.click(screen.getByRole('button', { name: '预览计划' }));

  expect(await screen.findByText(/职责：项目主创/)).toBeTruthy();
  expect(screen.getByText(/Skill：script-master/)).toBeTruthy();
  expect(screen.getByText(/node-1 · 画布版本 4 · fingerprint-1/)).toBeTruthy();
  expect(screen.getByText(/依赖顺序计划草稿/)).toBeTruthy();
  expect(screen.getByText('输出：candidate → canvas')).toBeTruthy();
  expect(plans.previewAgentPlan).toHaveBeenCalledWith('p', expect.objectContaining({
    canvas_id: 'canvas-1', conversation_id: 'conversation-1',
    selection: { kind: 'nodes', node_ids: ['node-1'] },
  }));

  fireEvent.change(screen.getByLabelText('任务目标 main'), { target: { value: '改写后的目标' } });
  fireEvent.click(screen.getByRole('button', { name: '保存编辑' }));
  await waitFor(() => expect(plans.editAgentPlan).toHaveBeenCalled());
  fireEvent.click(screen.getByRole('button', { name: '确认文本执行' }));
  await waitFor(() => expect(plans.confirmAgentPlan).toHaveBeenCalledWith('p', 'plan-1',
    { canvas_id: 'canvas-1', conversation_id: 'conversation-1' },
    expect.objectContaining({ permission: 'text', confirmed: true })));
});

it('edits multiple plan tasks and dependency edges before confirmation', async () => {
  render(<AgentPlanPanel owner={owner} nodes={runtimeNodes} />);
  fireEvent.change(screen.getByLabelText('Agent 任务描述'), { target: { value: '拆分开场任务' } });
  fireEvent.click(screen.getByRole('button', { name: '预览计划' }));
  await screen.findByText(/依赖顺序计划草稿/);
  fireEvent.click(screen.getByRole('button', { name: '添加任务' }));
  fireEvent.change(screen.getByLabelText('任务目标 task-2'), { target: { value: '检查人物动机' } });
  await userEvent.selectOptions(screen.getByLabelText('依赖 task-2'), 'main');
  fireEvent.click(screen.getByRole('button', { name: '保存编辑' }));
  await waitFor(() => expect(plans.editAgentPlan).toHaveBeenCalled());
  const editBody = vi.mocked(plans.editAgentPlan).mock.calls[0][3];
  expect(editBody.tasks).toHaveLength(2);
  expect(editBody.tasks[1].depends_on).toEqual(['main']);
  expect(editBody.tasks[1].goal).toBe('检查人物动机');
});

it('shows media plans as awaiting separate authorization and never offers text confirmation', async () => {
  vi.mocked(plans.previewAgentPlan).mockResolvedValue({ ...plan, required_permission: 'media',
    tasks: [{ ...plan.tasks[0], permission: 'media', operation: 'node.run',
      outputs: [{ type: 'image', target: 'canvas' }] }] } as never);
  render(<AgentPlanPanel owner={owner} nodes={runtimeNodes} />);
  fireEvent.change(screen.getByLabelText('Agent 任务描述'), { target: { value: '生成一张图' } });
  fireEvent.click(screen.getByRole('button', { name: '预览计划' }));
  expect(await screen.findByText(/需要单独的媒体授权/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: '确认文本执行' })).toBeNull();
  expect(plans.confirmAgentPlan).not.toHaveBeenCalled();
});
