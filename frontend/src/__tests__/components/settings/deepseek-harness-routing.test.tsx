import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';

import type { TaskRuntimeName } from '@/lib/queries/model-gateway';

const mocks = vi.hoisted(() => ({
  save: vi.fn().mockResolvedValue({ ok: true }),
  data: { data: {
    roles: [
      { id: 'director_plan', label: '导演规划', route: {
        runtime: 'codex', model: 'gpt-5.6-sol', reasoning_effort: 'medium',
        skill_id: null, skill_version: null, fallback: 'stop',
      } },
      { id: 'shot_plan', label: '分镜规划', route: {
        runtime: 'codex', model: 'gpt-5.6-sol', reasoning_effort: 'medium',
        skill_id: null, skill_version: null, fallback: 'stop',
      } },
    ],
    runtime_presets: {
      deepseek_harness: { model: 'deepseek-v4-flash-vision-exp', reasoning_effort: 'low' },
    },
  } },
  catalog: {
    data: {
      codex: ['gpt-5.6-sol', 'gpt-6-astra'],
      workbuddy: ['default-model'],
      model_api: [],
      deepseek_harness: ['deepseek-v4-flash-vision-exp', 'deepseek-v4-pro'],
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

it('exposes deepseek_harness as a task runtime name', () => {
  const names: TaskRuntimeName[] = ['codex', 'model_api', 'workbuddy', 'deepseek_harness'];
  expect(names).toHaveLength(4);
});

// 单个 render 内跑完整流程：同一文件里反复 render 时 base-ui 的 Select 会复用内部 id，
// 导致后续 render 的点击打不开下拉。因此所有断言点合并在同一个 it 里。
it('remembers presets per runtime, keeps the harness unified edit across switches, and renders DeepSeek Harness rows read-only', async () => {
  const user = userEvent.setup();
  render(<TextTaskRoutingPanel open />);
  const runtimeTrigger = (label = '导演规划') => screen.getByRole('combobox', { name: `${label}执行运行时` });
  const modelTrigger = (label = '导演规划') => screen.getByLabelText(`${label}模型`);
  const modelEditor = (label = '导演规划') => screen.getByLabelText(`${label}模型编辑`);
  const effortTrigger = (label = '导演规划') => screen.getByRole('combobox', { name: `${label}推理强度` });
  const harnessModelTrigger = () => screen.getByLabelText('DeepSeek Harness 统一模型');
  const harnessModelEditor = () => screen.getByLabelText('DeepSeek Harness 统一模型编辑');
  const switchRuntime = async (label: string, option: string) => {
    await user.click(runtimeTrigger(label));
    await user.click(await screen.findByRole('option', { name: option }));
  };

  // 1) 按运行时记忆：先切到 WorkBuddy 手填 wb-model。
  await switchRuntime('导演规划', 'WorkBuddy');
  expect(modelTrigger()).toHaveTextContent('default-model');
  await user.click(modelTrigger());
  await user.click(modelEditor());
  fireEvent.change(modelEditor(), { target: { value: 'wb-model' } });
  fireEvent.keyDown(modelEditor(), { key: 'Enter' });
  expect(modelTrigger()).toHaveTextContent('wb-model');

  // 2) 切回 Codex：显示 Codex 自己的记忆/默认值，而不是 wb-model。
  await switchRuntime('导演规划', 'Codex');
  expect(modelTrigger()).not.toHaveTextContent('wb-model');
  expect(modelTrigger()).toHaveTextContent('gpt-5.6-sol');

  // 3) 再切回 WorkBuddy：恢复记忆值 wb-model。
  await switchRuntime('导演规划', 'WorkBuddy');
  expect(modelTrigger()).toHaveTextContent('wb-model');

  // 4) harness 行只读：模型控件禁用并显示运行时统一模型；推理强度同样禁用。
  await switchRuntime('导演规划', 'DeepSeek Harness');
  expect(modelTrigger()).toBeDisabled();
  expect(modelTrigger()).toHaveTextContent('deepseek-v4-flash-vision-exp');
  expect(effortTrigger()).toBeDisabled();

  // 5) 第二个 harness 行：把「分镜规划」也切到 harness，两行都显示统一值。
  await switchRuntime('分镜规划', 'DeepSeek Harness');
  expect(modelTrigger('分镜规划')).toBeDisabled();
  expect(modelTrigger('分镜规划')).toHaveTextContent('deepseek-v4-flash-vision-exp');

  // 6) 修改「DeepSeek Harness 统一配置」→ 所有 harness 行同步显示新值。
  await user.click(harnessModelTrigger());
  await user.click(harnessModelEditor());
  fireEvent.change(harnessModelEditor(), { target: { value: 'B-harness' } });
  fireEvent.keyDown(harnessModelEditor(), { key: 'Enter' });
  expect(harnessModelTrigger()).toHaveTextContent('B-harness');
  expect(modelTrigger()).toHaveTextContent('B-harness');
  expect(modelTrigger('分镜规划')).toHaveTextContent('B-harness');

  // 7) 缺陷回归：把 harness 行切走再切回，统一配置里的编辑不能被行内旧值静默回退。
  await switchRuntime('导演规划', 'WorkBuddy');
  expect(modelTrigger()).toHaveTextContent('wb-model');
  await switchRuntime('导演规划', 'DeepSeek Harness');
  expect(harnessModelTrigger()).toHaveTextContent('B-harness');
  expect(modelTrigger()).toHaveTextContent('B-harness');
  expect(modelTrigger('分镜规划')).toHaveTextContent('B-harness');

  // 8) 保存时必须带上编辑后的 runtime_presets.deepseek_harness。
  await user.click(screen.getByRole('button', { name: '保存任务路由' }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledWith(expect.objectContaining({
    routes: expect.objectContaining({
      director_plan: expect.objectContaining({
        runtime: 'deepseek_harness',
        model: 'B-harness',
        reasoning_effort: 'low',
      }),
    }),
    runtime_presets: expect.objectContaining({
      deepseek_harness: {
        model: 'B-harness',
        reasoning_effort: 'low',
      },
    }),
  })));
});
