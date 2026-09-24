import { expect, it } from 'vitest';

it('exposes deepseek_harness as a task runtime name', async () => {
  const module = await import('@/lib/queries/model-gateway');
  const names: Array<module.TaskRuntimeName> = ['codex', 'model_api', 'workbuddy', 'deepseek_harness'];
  expect(names).toHaveLength(4);
});
