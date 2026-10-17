import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  getGenerationCraft: vi.fn(),
}));
const workflowApi = vi.hoisted(() => ({ recoverScreenplay: vi.fn() }));

vi.mock('@/features/script-creation/api', () => ({ scriptCreationApi: api }));
vi.mock('@/features/canvas/production/creativeWorkflowApi', () => ({ creativeWorkflowApi: workflowApi }));

import { RetainedScreenplayDraft } from '@/features/canvas/production/RetainedScreenplayDraft';

const records = [
  { step_key: 'episode_script:2', stage: 'draft', output: { markdown: '# E02 初稿\n\n林岚发现旧钥匙。' } },
  { step_key: 'episode_script:1', stage: 'repair', output: { markdown: '# E01 不应载入\n\n另一集内容。' } },
  { step_key: 'episode_script:2', stage: 'repair', output: { markdown: '# E02 修订稿\n\n林岚发现钥匙上的新刻痕。' } },
];

function mount(onCreated = vi.fn(async () => undefined)) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <RetainedScreenplayDraft project="project-1" runId="run-7" episode={2} onCreated={onCreated} />
    </QueryClientProvider>,
  );
  return { ...view, onCreated };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getGenerationCraft.mockResolvedValue({ records });
  workflowApi.recoverScreenplay.mockImplementation(async (_project, runId, draftIndex) => ({
    document: { id: `recovered-${draftIndex}`, kind: 'episode_script', episode_number: 2 },
    draft_index: draftIndex,
    run_id: runId,
  }));
});

it('loads retained drafts without creating a document until the user requests recovery', async () => {
  const onCreated = vi.fn(async () => undefined);
  mount(onCreated);

  await screen.findByRole('button', { name: '将草稿载入正文编辑' });
  expect(api.getGenerationCraft).toHaveBeenCalledWith('project-1', 'run-7');
  expect(workflowApi.recoverScreenplay).not.toHaveBeenCalled();
  expect(onCreated).not.toHaveBeenCalled();
});

it('defaults to the latest draft for this episode and explicitly recovers its exact index', async () => {
  const onCreated = vi.fn(async () => undefined);
  mount(onCreated);

  const choice = await screen.findByRole('combobox', { name: '选择保留草稿' });
  expect(choice).toHaveValue('1');
  fireEvent.click(screen.getByRole('button', { name: '将草稿载入正文编辑' }));

  await waitFor(() => expect(workflowApi.recoverScreenplay).toHaveBeenCalledWith(
    'project-1', 'run-7', 1, expect.any(String),
  ));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith({
    id: 'recovered-1', kind: 'episode_script', episode_number: 2,
  }));
});

it('recovers the exact initially selected draft when the user chooses it over the default latest draft', async () => {
  mount();

  const choice = await screen.findByRole('combobox', { name: '选择保留草稿' });
  fireEvent.change(choice, { target: { value: '0' } });
  const preview = screen.getByText((_, element) => element?.tagName === 'PRE');
  expect(preview.textContent).toBe('# E02 初稿\n\n林岚发现旧钥匙。');
  fireEvent.click(screen.getByRole('button', { name: '将草稿载入正文编辑' }));

  await waitFor(() => expect(workflowApi.recoverScreenplay).toHaveBeenCalledWith(
    'project-1', 'run-7', 0, expect.any(String),
  ));
});

it('keeps a conflict visible and does not open a fallback document after transactional recovery fails', async () => {
  const onCreated = vi.fn(async () => undefined);
  const user = userEvent.setup();
  workflowApi.recoverScreenplay.mockRejectedValue(new Error('HTTP 409: same-episode document already exists'));
  mount(onCreated);

  await user.click(await screen.findByRole('button', { name: '将草稿载入正文编辑' }));

  expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 409: same-episode document already exists');
  expect(workflowApi.recoverScreenplay).toHaveBeenCalledWith('project-1', 'run-7', 1, expect.any(String));
  expect(onCreated).not.toHaveBeenCalled();
});
