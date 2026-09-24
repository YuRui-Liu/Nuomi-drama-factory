import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  save: vi.fn().mockResolvedValue({ ok: true }),
  data: { data: {
    roles: [{ id: 'director_plan', label: '导演规划', route: {
      runtime: 'codex', model: 'gpt-5.6-sol', reasoning_effort: 'medium',
      skill_id: null, skill_version: null, fallback: 'stop',
    } }],
    runtime_presets: {},
  } },
  catalog: {
    data: {
      codex: ['gpt-5.6-sol', 'gpt-6-astra'],
      workbuddy: ['default-model', 'gpt-5.6-sol', 'Hy4 preview'],
      model_api: [],
      deepseek_harness: [],
    },
  },
}));
vi.mock('@/lib/queries/model-gateway', () => ({
  TASK_REASONING_EFFORTS: ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'],
  useTaskRuntimeConfig: () => ({ data: mocks.data }),
  useTaskRuntimeModels: () => ({ data: mocks.catalog }),
  useSaveTaskRuntimeConfig: () => ({ mutateAsync: mocks.save, isPending: false }),
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
import { TextTaskRoutingPanel } from '@/components/settings/text-task-routing-panel';
afterEach(cleanup);

// 单个 render 内跑完整流程：同一文件里反复 render 时 base-ui 的 Select 会复用内部 id，
// 导致后续 render 的点击打不开下拉。
it('routes WorkBuddy with per-runtime model memory, a default model placeholder and max effort', async () => {
  const user = userEvent.setup();
  render(<TextTaskRoutingPanel open />);
  const runtimeTrigger = () => screen.getByRole('combobox', { name: '导演规划执行运行时' });
  const modelTrigger = () => screen.getByLabelText('导演规划模型');
  const modelEditor = () => screen.getByLabelText('导演规划模型编辑');
  const modelOptions = () =>
    Array.from(document.querySelectorAll<HTMLLIElement>('[aria-label="导演规划模型候选"] [role="option"]'))
      .map((li) => li.textContent?.trim() ?? '');

  // 1) 没有 WorkBuddy 记忆值时，切到 WorkBuddy 才写入 default-model。
  // 先清空当前模型：点开弹窗 → 编辑输入框清空 → 回车提交。
  await user.click(modelTrigger());
  await user.click(modelEditor());
  fireEvent.change(modelEditor(), { target: { value: '' } });
  fireEvent.keyDown(modelEditor(), { key: 'Enter' });
  await user.click(runtimeTrigger());
  await user.click(await screen.findByRole('option', { name: 'WorkBuddy' }));
  expect(modelTrigger()).toHaveTextContent('default-model');

  // 2) 模型下拉按运行时显示候选清单（workbuddy 列表里有 Hy4 preview 这种带空格 ID）。
  await user.click(modelTrigger());
  expect(modelOptions()).toEqual(['default-model', 'gpt-5.6-sol', 'Hy4 preview']);

  // 3) 按运行时记忆：手填的模型只记在 WorkBuddy 上；切到 Codex 用 Codex 自己的记忆/默认，
  //    切回 WorkBuddy 才恢复刚填的值。（旧行为「跨 runtime 保留同一个值」已被替换。）
  await user.click(modelEditor());
  fireEvent.change(modelEditor(), { target: { value: 'my-workbuddy-model' } });
  fireEvent.keyDown(modelEditor(), { key: 'Enter' });
  await user.click(runtimeTrigger());
  await user.click(await screen.findByRole('option', { name: 'Codex' }));
  await user.click(modelTrigger());
  expect(modelOptions()).toEqual(['gpt-5.6-sol', 'gpt-6-astra']);
  fireEvent.keyDown(modelEditor(), { key: 'Escape' });
  // Codex 的记忆值来自切走时记下的 Codex 行（此处为空模型，显示占位默认 gpt-5.6-sol）。
  expect(modelTrigger()).not.toHaveTextContent('my-workbuddy-model');
  expect(modelTrigger()).toHaveTextContent('gpt-5.6-sol');
  await user.click(runtimeTrigger());
  await user.click(await screen.findByRole('option', { name: 'WorkBuddy' }));
  expect(modelTrigger()).toHaveTextContent('my-workbuddy-model');

  // 4) WorkBuddy 下推理强度可选，且包含其专属的 max 档位。
  const effort = screen.getByRole('combobox', { name: '导演规划推理强度' });
  expect(effort).not.toBeDisabled();
  await user.click(effort);
  await user.click(await screen.findByRole('option', { name: 'max（仅 WorkBuddy）' }));

  await user.click(screen.getByRole('button', { name: '保存任务路由' }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledWith(expect.objectContaining({
    routes: expect.objectContaining({
      director_plan: expect.objectContaining({
        runtime: 'workbuddy',
        model: 'my-workbuddy-model',
        reasoning_effort: 'max',
      }),
    }),
    runtime_presets: expect.objectContaining({
      workbuddy: expect.objectContaining({ model: 'my-workbuddy-model' }),
    }),
  })));
});
