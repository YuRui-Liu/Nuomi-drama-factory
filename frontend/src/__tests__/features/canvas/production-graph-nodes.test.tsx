import { fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReactFlowProvider, type NodeProps } from '@xyflow/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { EpisodeComposeNode } from '@/features/canvas/nodes/EpisodeComposeNode';
import { NarrativeGroupVideoNode } from '@/features/canvas/nodes/NarrativeGroupVideoNode';
import { WorkspaceScopeProvider } from '@/features/freezone/WorkspaceScope';

const mocks = vi.hoisted(() => ({
  store: {
    setSelectedNode: vi.fn(),
    requestFocusNode: vi.fn(),
    updateNodeData: vi.fn(),
  },
}));

vi.mock('@/lib/url-params', () => ({ readUrl: () => ({ project: 'project' }) }));
vi.mock('@/stores/canvasStore', () => ({
  useCanvasStore: (select: (state: typeof mocks.store) => unknown) => select(mocks.store),
}));
vi.mock('@/features/canvas/ui/NodeHeader', () => ({
  NodeHeader: ({ titleText }: { titleText: string }) => <h2>{titleText}</h2>,
  NODE_HEADER_FLOATING_POSITION_CLASS: '',
}));

beforeEach(() => vi.clearAllMocks());

function props(id: string, data: Record<string, unknown>) {
  return { id, data } as unknown as NodeProps;
}

function renderNode(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><ReactFlowProvider>{node}</ReactFlowProvider></QueryClientProvider>);
}

it('shows the group video purpose without exposing a duplicate run-target button', () => {
  renderNode(<NarrativeGroupVideoNode {...props('video-a', {
    episodeNumber: 2, groupId: 'group-a', directorRevisionId: 'revision-7', operation: 'video',
  })} />);

  expect(screen.getByText('叙事组 group-a · 固定版本视频制作')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '选择此节点作为运行目标' })).not.toBeInTheDocument();
});

it('saves an explicit native audio choice only in its bound episode canvas without starting production', () => {
  renderNode(<WorkspaceScopeProvider value={{ projectId: 'project', canvasId: 'canvas', kind: 'episode', episodeId: 'e2', episodeNumber: 2, status: 'applied' }}>
    <NarrativeGroupVideoNode {...props('video-a', { episodeNumber: 2, groupId: 'group-a', directorRevisionId: 'revision-7' })} />
  </WorkspaceScopeProvider>);
  fireEvent.change(screen.getByRole('combobox', { name: '组视频声音来源' }), { target: { value: 'h3_native' } });
  expect(mocks.store.updateNodeData).toHaveBeenCalledWith('video-a', { dialogueSource: 'h3_native' });
  expect(mocks.store.setSelectedNode).not.toHaveBeenCalled();
});

it('keeps audio settings read-only outside the bound episode canvas', () => {
  renderNode(<NarrativeGroupVideoNode {...props('video-a', { episodeNumber: 2, groupId: 'group-a', directorRevisionId: 'revision-7' })} />);
  expect(screen.getByRole('combobox', { name: '组视频声音来源' })).toBeDisabled();
});

it('shows the compose target and requires a complete fixed production binding', () => {
  renderNode(<EpisodeComposeNode {...props('compose-a', {
    episodeNumber: 2, directorRevisionId: 'revision-7', productionGroupIds: ['group-a', 'group-b'], operation: 'compose',
  })} />);

  expect(screen.getByText('汇总 2 个叙事组 · 运行此节点开始整集合成')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '选择此节点作为运行目标' })).not.toBeInTheDocument();
});

it('does not offer an invalid compose node as a run target', () => {
  renderNode(<EpisodeComposeNode {...props('compose-invalid', { episodeNumber: 0 })} />);

  expect(screen.getByRole('alert')).toHaveTextContent('固定导演版本');
  expect(screen.queryByRole('button', { name: '选择此节点作为运行目标' })).not.toBeInTheDocument();
  expect(mocks.store.setSelectedNode).not.toHaveBeenCalled();
});

it('shows the latest projected execution status and failure reason on the group video node', () => {
  renderNode(<NarrativeGroupVideoNode {...props('video-failed', {
    episodeNumber: 2, groupId: 'group-a', directorRevisionId: 'revision-7', operation: 'video',
    orchestration: { status: 'failed', error: { code: 'provider_error', message: '视频服务渲染失败' } },
  })} />);

  expect(screen.getByRole('status')).toHaveTextContent('执行失败');
  expect(screen.getByRole('alert')).toHaveTextContent('视频服务渲染失败');
});
