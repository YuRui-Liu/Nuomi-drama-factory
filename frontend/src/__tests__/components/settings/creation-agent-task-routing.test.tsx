import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  save: vi.fn().mockResolvedValue({ ok: true }),
  data: { data: {
    roles: [
      { id: 'script_creation', label: '剧本创作', route: {
        runtime: 'codex', model: 'gpt-6-luna', reasoning_effort: 'medium',
        skill_id: null, skill_version: null, fallback: 'stop',
      } },
      { id: 'creation_agent_decision', label: '创作主创Agent', route: {
        runtime: 'codex', model: 'gpt-6-luna', reasoning_effort: 'medium',
        skill_id: null, skill_version: null, fallback: 'stop',
      } },
    ],
    runtime_presets: {
      codex: { model: 'gpt-5.6-sol', reasoning_effort: 'medium' },
    },
  } },
  catalog: { data: {
    codex: ['gpt-6-astra', 'gpt-6-sol', 'gpt-6-luna', 'gpt-5.6-sol'],
    workbuddy: [],
    model_api: [],
    deepseek_harness: [],
  } },
}));

vi.mock('@/lib/queries/model-gateway', () => ({
  TASK_REASONING_EFFORTS: ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'],
  useTaskRuntimeConfig: () => ({ data: mocks.data }),
  useTaskRuntimeModels: () => ({ data: mocks.catalog }),
  useSaveTaskRuntimeConfig: () => ({ mutateAsync: mocks.save, isPending: false }),
}));
vi.mock('@/components/settings/chat-runtime-panel', () => ({ ChatRuntimePanel: () => null }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { TextTaskRoutingPanel } from '@/components/settings/text-task-routing-panel';

afterEach(cleanup);

it('renders and saves the creation decision route supplied by the task-runtime API', async () => {
  const user = userEvent.setup();
  render(<TextTaskRoutingPanel open />);

  const modelControl = await screen.findByLabelText('创作主创Agent模型');
  expect(screen.getByRole('combobox', { name: '创作主创Agent执行运行时' })).toHaveTextContent('Codex');
  expect(modelControl).toHaveTextContent('gpt-6-luna');
  expect(screen.getByRole('combobox', { name: '创作主创Agent推理强度' })).toHaveTextContent('medium');

  await user.click(modelControl);
  expect(await screen.findByRole('option', { name: 'gpt-6-luna' })).toBeInTheDocument();
  await user.keyboard('{Escape}');
  await user.click(screen.getByRole('button', { name: '保存任务路由' }));

  await waitFor(() => expect(mocks.save).toHaveBeenCalledWith({
    routes: expect.objectContaining({
      script_creation: expect.objectContaining({
        runtime: 'codex', model: 'gpt-6-luna', reasoning_effort: 'medium', fallback: 'stop',
      }),
      creation_agent_decision: expect.objectContaining({
        runtime: 'codex', model: 'gpt-6-luna', reasoning_effort: 'medium', fallback: 'stop',
      }),
    }),
    runtime_presets: {
      codex: { model: 'gpt-5.6-sol', reasoning_effort: 'medium' },
    },
  }));
});
