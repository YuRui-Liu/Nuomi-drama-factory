import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
const call = vi.hoisted(() => vi.fn());
vi.mock('@/api/client', () => ({ apiCall: call }));
vi.mock('@/lib/queries/task-concurrency', () => ({ useTaskConcurrency: () => ({ data: { lanes: { default: { configured: 3 }, video: { configured: 5 } }, restart_required: false } }) }));
import { ProductionPreflight } from '@/features/canvas/production/ProductionPreflight';

it('shows saved limits and missing required references using read-only requests for selected groups', async () => {
  call.mockResolvedValue({ reference_revision: 'refs-2', bindings: [{ binding_id: 'b', display_label: '林澈', status: 'missing_image', required: true, issue_message: '缺少身份图', repair_action: '生成身份图' }] });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ProductionPreflight project="p" episode={1} revisionId="dir-1" groupIds={['g1']} settings={{ g1: { revision: 2, workflow_id: 'saved-workflow', overrides: { aspect_ratio: '9:16' } } }} /></QueryClientProvider>);
  expect(screen.getByText('保存并发：图像 3，视频 5')).toBeInTheDocument();
  expect(await screen.findByText(/林澈：缺少身份图/)).toBeInTheDocument();
  expect(screen.getByText(/saved-workflow/)).toBeInTheDocument();
  expect(call).toHaveBeenCalledTimes(1);
  expect(call).toHaveBeenCalledWith('projects/p/episodes/1/narrative-groups/g1/render/references');
});
