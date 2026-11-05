import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { ScriptNode } from '@/features/canvas/nodes/ScriptNode';
import { useCanvasStore } from '@/stores/canvasStore';
import {
  CANVAS_NODE_TYPES,
  type CanvasNode,
  type ScriptNodeData,
  type StoryboardScriptRow,
} from '@/features/canvas/domain/canvasNodes';

const mocks = vi.hoisted(() => ({ upstream: [] as CanvasNode[] }));
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
  ...await vi.importActual('@/api/ops'), submitFreezoneStoryScript: vi.fn(), fetchFreezoneStoryScriptResult: vi.fn(),
}));
vi.mock('@/api/tasks', async () => ({ ...await vi.importActual('@/api/tasks'), awaitTaskCompletion: vi.fn() }));

const directorRow: StoryboardScriptRow = {
  shot_no: 1,
  duration: 6,
  visual_description: '手停在近前→抬手→未接触',
  shot: '近景',
  director: {
    framing: '近景 · 平视',
    blocking: 'A→B · 机位左',
    lights: '右后逆光 · 冷',
    performance: '紧张克制',
    props: '背包带垂下',
    continuity: '东侧不越轴',
    panel_spec: '近景：平视，左三分；屏幕运动 左→右；无世界坐标。',
  },
};

const plainRow: StoryboardScriptRow = {
  shot_no: 2,
  duration: 7,
  visual_description: '侧身→转身→追向面外',
  shot: '中景',
};

const otherDirectorRow: StoryboardScriptRow = {
  shot_no: 3,
  duration: 5,
  visual_description: '空镜→冲刺→拐角消失',
  director: { framing: '全景 · 俯拍', blocking: 'C→D · 机位右', lights: '顶光' },
};

function mountScriptNode(data: ScriptNodeData, selected = true) {
  useCanvasStore.setState({
    nodes: [{ id: 'script', type: CANVAS_NODE_TYPES.script, position: { x: 0, y: 0 }, data }],
    edges: [],
    selectedNodeId: null,
  });
  const node = useCanvasStore.getState().nodes[0];
  return render(
    <ScriptNode
      {...({ ...node, selected } as unknown as React.ComponentProps<typeof ScriptNode>)}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.upstream = [];
  window.history.replaceState({}, '', '/projects/demo/freezone?canvas=storyboard-director');
});

it('expands the current row director block inline when the node is selected', () => {
  mountScriptNode({ scriptResult: { title: 'E01 分镜表', rows: [directorRow, plainRow] } });

  const block = screen.getByTestId('storyboard-director-block');
  expect(within(block).getByText('S01 · director 结构化块')).toBeInTheDocument();
  expect(within(block).getByText('景别')).toBeInTheDocument();
  expect(within(block).getByText('近景 · 平视')).toBeInTheDocument();
  expect(within(block).getByText('轴线')).toBeInTheDocument();
  expect(within(block).getByText('A→B · 机位左')).toBeInTheDocument();
  expect(within(block).getByText('主光')).toBeInTheDocument();
  expect(within(block).getByText('右后逆光 · 冷')).toBeInTheDocument();
  expect(within(block).getByText('表演')).toBeInTheDocument();
  expect(within(block).getByText('道具')).toBeInTheDocument();
  expect(within(block).getByText('连续性')).toBeInTheDocument();
  expect(within(block).getByText(/无世界坐标/)).toBeInTheDocument();
});

it('keeps the block collapsed while the node is not selected', () => {
  mountScriptNode({ scriptResult: { rows: [directorRow] } }, false);

  expect(screen.getByRole('table')).toBeInTheDocument();
  expect(screen.queryByTestId('storyboard-director-block')).not.toBeInTheDocument();
});

it('does not render an inline block when no row carries a director', () => {
  mountScriptNode({ scriptResult: { rows: [plainRow] } });

  expect(screen.getByRole('table')).toBeInTheDocument();
  expect(screen.queryByTestId('storyboard-director-block')).not.toBeInTheDocument();
  expect(screen.queryByText(/director 结构化块/)).not.toBeInTheDocument();
});

it('moves the inline block to the row the user picks', () => {
  mountScriptNode({ scriptResult: { rows: [directorRow, otherDirectorRow] } });
  expect(within(screen.getByTestId('storyboard-director-block')).getByText('S01 · director 结构化块')).toBeInTheDocument();

  // [0] 表头、[1] S01、[2] S01 展开行、[3] S03、[4] S03 展开行（点行后出现）。
  fireEvent.click(screen.getAllByRole('row')[3]);

  const block = screen.getByTestId('storyboard-director-block');
  expect(within(block).getByText('S03 · director 结构化块')).toBeInTheDocument();
  expect(within(block).getByText('全景 · 俯拍')).toBeInTheDocument();
});

it('shows the projection badge with the first six hash characters', () => {
  mountScriptNode({
    scriptResult: { rows: [directorRow] },
    directorBinding: { episode: 1, revisionId: 'rev-12', projectionHash: '9f2c4a7b1e' },
  });

  const badge = screen.getByTestId('storyboard-director-badge');
  expect(badge).toHaveTextContent('计划投影 · hash 9f2c4a');
  expect(badge).not.toHaveTextContent('9f2c4a7b1e');
  expect(badge).toHaveAttribute('title', '导演计划 rev-12 · 第 1 集');
});

it('omits the projection badge when the table is not a director projection', () => {
  mountScriptNode({ scriptResult: { rows: [directorRow] } });

  expect(screen.queryByTestId('storyboard-director-badge')).not.toBeInTheDocument();
});
