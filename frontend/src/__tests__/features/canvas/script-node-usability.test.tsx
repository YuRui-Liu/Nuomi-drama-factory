import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { ScriptNode } from '@/features/canvas/nodes/ScriptNode';
import { useCanvasStore } from '@/stores/canvasStore';
import { CANVAS_NODE_TYPES, type CanvasNode } from '@/features/canvas/domain/canvasNodes';

const mocks = vi.hoisted(() => ({ upstream: [] as CanvasNode[], submit: vi.fn(), result: vi.fn() }));
vi.mock('@xyflow/react', async () => ({
  ...await vi.importActual('@xyflow/react'), Handle: () => null, useUpdateNodeInternals: () => () => {},
}));
vi.mock('@/features/canvas/ui/NodeResizeHandle', () => ({ NodeResizeHandle: () => null }));
vi.mock('@/features/canvas/ui/NodeHeader', () => ({ NODE_HEADER_FLOATING_POSITION_CLASS: '', NodeHeader: () => null }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/features/canvas/application/useUpstreamGraph', () => ({ useUpstreamNodes: () => mocks.upstream }));
vi.mock('@/features/canvas/hooks/useNodeGenerationHistory', () => ({ useNodeGenerationHistory: () => ({ records: [], isLoading: false, refresh: vi.fn() }) }));
vi.mock('@/lib/queries/generation-credit-cost', () => ({ useGenerationCreditCost: () => ({}) }));
vi.mock('@/api/ops', async () => ({
  ...await vi.importActual('@/api/ops'), submitFreezoneStoryScript: mocks.submit, fetchFreezoneStoryScriptResult: mocks.result,
}));
vi.mock('@/api/tasks', async () => ({ ...await vi.importActual('@/api/tasks'), awaitTaskCompletion: vi.fn() }));

function mount(selected = true) {
  const node = useCanvasStore.getState().nodes[0];
  return render(<ScriptNode {...({ ...node, selected } as unknown as React.ComponentProps<typeof ScriptNode>)} />);
}
beforeEach(() => {
  vi.clearAllMocks();
  mocks.upstream = [];
  window.history.replaceState({}, '', '/projects/demo/freezone?canvas=check');
  useCanvasStore.setState({ nodes: [{ id: 'script', type: CANVAS_NODE_TYPES.script, position: { x: 0, y: 0 }, data: { prompt: '' } }], edges: [], selectedNodeId: null });
});

it('lets a new standalone script node accept a prompt', () => {
  mount();
  expect(screen.getByRole('textbox')).toBeVisible();
  expect(screen.getByTitle('生成')).toBeDisabled();
});

it('shows the next step instead of a blank connected node', () => {
  mocks.upstream = [{ id: 'text', type: CANVAS_NODE_TYPES.textAnnotation, position: { x: 0, y: 0 }, data: { content: '雨夜，邮差送来一封迟到的信。' } }];
  mount(false);
  expect(screen.getByText(/点击节点.*生成分镜脚本/)).toBeVisible();
});

it('requires text when only visual references are connected', () => {
  mocks.upstream = [{ id: 'image', type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl: '/static/test.png', aspectRatio: '1:1' } }];
  mount();
  expect(screen.getByTitle('生成')).toBeDisabled();
  expect(screen.getByText(/不会自动解析图片或视频/)).toBeVisible();
});

it('submits upstream text and stores returned storyboard rows', async () => {
  mocks.upstream = [{ id: 'text', type: CANVAS_NODE_TYPES.textAnnotation, position: { x: 0, y: 0 }, data: { content: '雨夜，邮差送来一封迟到的信。' } }];
  mocks.submit.mockResolvedValue({ task_key: 'test-task', job_id: 'test-job' });
  mocks.result.mockResolvedValue({ title: '迟到的信', rows: [{ shot_no: 1, visual_description: '雨中的邮差' }] });
  mount();
  fireEvent.click(screen.getByTitle('生成'));
  await waitFor(() => expect(mocks.submit).toHaveBeenCalledWith('demo', expect.objectContaining({ sourceText: '雨夜，邮差送来一封迟到的信。', canvasId: 'check', nodeId: 'script' })));
  await waitFor(() => expect(useCanvasStore.getState().nodes[0].data.scriptResult).toEqual({ title: '迟到的信', rows: [{ shot_no: 1, visual_description: '雨中的邮差' }] }));
});
