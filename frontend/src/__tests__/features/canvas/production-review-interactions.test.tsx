import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  activateSemantics: vi.fn(),
  continue: vi.fn(),
  screenplayReview: vi.fn(),
  reviewScreenplay: vi.fn(),
}));

vi.mock('@/features/canvas/production/directorWorkspaceApi', () => ({
  directorWorkspaceApi: {
    activateSemantics: api.activateSemantics,
    continue: api.continue,
  },
}));
vi.mock('@/features/canvas/production/creativeWorkflowApi', () => ({
  creativeWorkflowApi: {
    screenplayReview: api.screenplayReview,
    reviewScreenplay: api.reviewScreenplay,
  },
}));

import { ManualScreenplayReview } from '@/features/canvas/production/ManualScreenplayReview';
import { SemanticReview } from '@/features/canvas/production/SemanticReview';

it('waits for the original review receipt instead of allowing another submission when delivery is unknown', async () => {
  api.screenplayReview.mockResolvedValue({ status: 'submission_unknown' });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ManualScreenplayReview project="project-1" documentId="screenplay-1" revisionId="rev-unknown" planRunId="plan-1" saved />
    </QueryClientProvider>,
  );
  await screen.findByText('提交结果待确认，正在查询原任务');
  const submit = screen.getByRole('button', { name: '提交当前版独立审稿' });
  expect(submit).toBeDisabled();
  fireEvent.click(submit);
  expect(api.reviewScreenplay).not.toHaveBeenCalled();
});

function candidate(revisionId: string, canActivate: boolean) {
  return {
    revision_id: revisionId,
    status: 'candidate',
    can_activate: canActivate,
    is_current_source: true,
    validation_report: { passed: canActivate, issues: [] },
    scenes: [{
      id: `scene-${revisionId}`,
      heading: '内景·档案室·夜',
      location: '档案室',
      time_of_day: '夜',
      characters: ['林岚'],
      blocks: [{ id: `line-${revisionId}`, text: '门外传来脚步声。' }],
    }],
    beats: [{
      id: `beat-${revisionId}`,
      scene_id: `scene-${revisionId}`,
      goal: '藏起文件',
      obstacle: '有人靠近',
      action: '关掉灯',
      reaction: '停下呼吸',
      turn: '脚步声停在门外',
      result: '文件仍在桌上',
      emotional_shift: '急迫转为恐惧',
    }],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.activateSemantics.mockResolvedValue({});
  api.continue.mockResolvedValue({});
  api.screenplayReview.mockResolvedValue(null);
  api.reviewScreenplay.mockResolvedValue({ status: 'pending' });
});

it('requires explicit adoption before continuing and disables a candidate that cannot activate', async () => {
  const refresh = vi.fn(async () => undefined);
  const candidates = [candidate('blocked-rev', false), candidate('ready-rev', true)];
  const view = render(
    <SemanticReview
      project="project-1"
      episode={4}
      semantics={{ active_revision_id: null, current_source_revision: 8, candidates }}
      refresh={refresh}
    />,
  );

  expect(api.activateSemantics).not.toHaveBeenCalled();
  expect(api.continue).not.toHaveBeenCalled();
  const adoptionButtons = screen.getAllByRole('button', { name: '采用此拆解' });
  expect(adoptionButtons[0]).toBeDisabled();
  expect(adoptionButtons[1]).toBeEnabled();
  expect(screen.getByRole('button', { name: '依据已采用拆解生成导演方案' })).toBeDisabled();

  fireEvent.click(adoptionButtons[1]);
  await waitFor(() => expect(api.activateSemantics).toHaveBeenCalledWith('project-1', 4, 'ready-rev'));
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  expect(api.continue).not.toHaveBeenCalled();

  view.rerender(
    <SemanticReview
      project="project-1"
      episode={4}
      semantics={{ active_revision_id: 'ready-rev', current_source_revision: 8, candidates }}
      refresh={refresh}
    />,
  );
  const continueButton = screen.getByRole('button', { name: '依据已采用拆解生成导演方案' });
  expect(continueButton).toBeEnabled();
  fireEvent.click(continueButton);
  await waitFor(() => expect(api.continue).toHaveBeenCalledWith('project-1', 4, 'ready-rev'));
});

it('requires a saved screenplay and reviews the exact current revision against its plan run', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <ManualScreenplayReview project="project-1" documentId="screenplay-1" revisionId="rev-old" planRunId="plan-old" saved={false} />
    </QueryClientProvider>,
  );

  const submit = screen.getByRole('button', { name: '提交当前版独立审稿' });
  expect(submit).toBeDisabled();
  await waitFor(() => expect(api.screenplayReview).toHaveBeenCalledWith('project-1', 'screenplay-1', 'rev-old'));
  expect(api.reviewScreenplay).not.toHaveBeenCalled();

  view.rerender(
    <QueryClientProvider client={client}>
      <ManualScreenplayReview project="project-1" documentId="screenplay-1" revisionId="rev-current" planRunId="plan-current" saved />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(api.screenplayReview).toHaveBeenCalledWith('project-1', 'screenplay-1', 'rev-current'));
  expect(submit).toBeEnabled();

  fireEvent.click(submit);
  await waitFor(() => expect(api.reviewScreenplay).toHaveBeenCalledWith(
    'project-1', 'screenplay-1', 'rev-current', 'plan-current', expect.any(String),
  ));
});
