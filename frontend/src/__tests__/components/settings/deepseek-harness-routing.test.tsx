import { expect, it } from 'vitest';

import type { TaskRuntimeName } from '@/lib/queries/model-gateway';

it('exposes deepseek_harness as a task runtime name', () => {
  const names: TaskRuntimeName[] = ['codex', 'model_api', 'workbuddy', 'deepseek_harness'];
  expect(names).toHaveLength(4);
});
