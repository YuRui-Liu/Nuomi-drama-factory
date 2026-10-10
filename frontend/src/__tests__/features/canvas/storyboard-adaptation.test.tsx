import { render, screen, fireEvent } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { StoryboardAdaptation } from '@/features/canvas/director/StoryboardAdaptation';
import { createDirectorDraft } from '@/features/canvas/domain/videoDirectorDraft';
import type { VideoDirectorNodeData } from '@/features/canvas/domain/canvasNodes';

const preparedData = (): VideoDirectorNodeData => ({ draft: createDirectorDraft('s'), activeAttemptId: null, videoUrl: null, resultRevision: null,
  storyboardPreparation: { inputMode: 'ref', sourceNodeId: 'board', sourceUrl: '/board.png', sourceWidth: 1000, sourceHeight: 1000,
    sourceFingerprint: 'f', settingsFingerprint: 's', status: 'prepared', baselineDraft: {}, requestedDurationSeconds: 18,
    actualDurationSeconds: 20.6667, actualTotalFrames: 496,
    cells: [1, 2, 3, 4].map((id) => ({ id: String(id), rect: [0, 0, 500, 500], cropUrl: `/crop${id}.png`, imageUrl: `/cell${id}.png` })) } });

it('uses the current 3x3 grid and never submits old 2x2 crop bounds', () => {
  const prepare = vi.fn();
  const change = vi.fn();
  const data = preparedData();
  const source = { gridRows: 2, gridCols: 2, imageUrl: '/board.png', frames: Array.from({ length: 9 }, (_, i) => ({ description: `Shot ${i + 1}` })),
    cellRects: data.storyboardPreparation!.cells.map((cell) => cell.rect) };
  const view = render(<StoryboardAdaptation data={data} source={source} prepare={prepare} busy={false} conflict={false} onSourceChange={change} />);
  view.rerender(<StoryboardAdaptation data={data} source={{ ...source, gridRows: 3, gridCols: 3 }} prepare={prepare} busy={false} conflict={false} onSourceChange={change} />);
  expect(screen.getAllByRole('textbox')).toHaveLength(9);
  fireEvent.change(screen.getByLabelText('镜头 9 描述'), { target: { value: 'Ninth action' } });
  expect(change.mock.calls[change.mock.calls.length - 1]?.[0].frames).toHaveLength(9);
  fireEvent.click(screen.getByRole('button', { name: '准备 / 刷新分镜参考' }));
  expect(prepare.mock.calls[0][0].shots).toHaveLength(9);
  expect(prepare.mock.calls[0][0]).not.toHaveProperty('rects');
});

it('marks changed source images stale and hides old previews and timing', () => {
  const prepare = vi.fn();
  const data = preparedData();
  const view = render(<StoryboardAdaptation data={data} source={{ imageUrl: '/board.png', gridRows: 2, gridCols: 2 }} prepare={prepare} busy={false} conflict={false} />);
  expect(screen.getAllByRole('img')).toHaveLength(4);
  view.rerender(<StoryboardAdaptation data={data} source={{ imageUrl: '/regenerated.png', gridRows: 2, gridCols: 2 }} prepare={prepare} busy={false} conflict={false} />);
  expect(screen.getByRole('alert')).toHaveTextContent('分镜来源已变化');
  expect(screen.queryAllByRole('img')).toHaveLength(0);
  expect(screen.queryByText(/20.67/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '准备 / 刷新分镜参考' }));
  expect(prepare.mock.calls[0][0]).not.toHaveProperty('rects');
});

it('detects source identity and same-count grid layout changes', () => {
  const data = preparedData();
  data.storyboardPreparation!.sourceGridRows = 2;
  data.storyboardPreparation!.sourceGridCols = 2;
  const props = { data, prepare: vi.fn(), busy: false, conflict: false };
  const view = render(<StoryboardAdaptation {...props} sourceNodeId="different-board" source={{ imageUrl: '/board.png', gridRows: 2, gridCols: 2 }} />);
  expect(screen.getByRole('alert')).toHaveTextContent('分镜来源已变化');
  view.rerender(<StoryboardAdaptation {...props} sourceNodeId="board" source={{ imageUrl: '/board.png', gridRows: 1, gridCols: 4 }} />);
  expect(screen.getByRole('alert')).toHaveTextContent('分镜来源已变化');
  expect(screen.getAllByRole('textbox')).toHaveLength(4);
});

it('shows Ref adaptation with cell previews and marks a previous Frames preparation stale', () => {
  const data = preparedData();
  data.storyboardPreparation!.inputMode = 'ref';
  data.activeInputMode = 'ref';
  data.inputModeIntent = 'manual';
  const props = { data, prepare: vi.fn(), busy: false, conflict: false };
  const view = render(<StoryboardAdaptation {...props} />);
  expect(screen.getByText(/Picture 1/)).toBeInTheDocument();
  expect(screen.getAllByRole('img')).toHaveLength(4);
  expect(screen.queryByAltText('镜头 1 单格首帧')).not.toBeInTheDocument();
  view.rerender(<StoryboardAdaptation {...props} data={{ ...data, storyboardPreparation: { ...data.storyboardPreparation!, inputMode: 'frames' } }} />);
  expect(screen.getByRole('alert')).toHaveTextContent('重新准备');
  view.rerender(<StoryboardAdaptation {...props} data={{ ...data, activeInputMode: 'frames', inputModeIntent: 'manual',
    storyboardPreparation: { ...data.storyboardPreparation!, inputMode: 'frames' } }} />);
  expect(screen.getByAltText('镜头 1 单格首帧')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '准备 / 刷新分镜首帧' })).toBeInTheDocument();
  view.rerender(<StoryboardAdaptation {...props} data={{ ...data, storyboardPreparation: { ...data.storyboardPreparation!, inputMode: undefined } }} />);
  expect(screen.getByRole('alert')).toHaveTextContent('重新准备');
});

it('shows all cell previews, editable descriptions, crop bounds and quantized timing', () => {
  const prepare = vi.fn();
  render(<StoryboardAdaptation data={{ draft: createDirectorDraft('s'), activeAttemptId: null, videoUrl: null, resultRevision: null,
    storyboardPreparation: { inputMode: 'ref', sourceNodeId: 'board', sourceUrl: '/board.png', sourceWidth: 1000, sourceHeight: 1000,
      sourceFingerprint: 'f', settingsFingerprint: 's', status: 'prepared', baselineDraft: {}, requestedDurationSeconds: 18,
      actualDurationSeconds: 20.6667, actualTotalFrames: 496,
      cells: [1, 2, 3, 4].map((id) => ({ id: String(id), rect: [0, 0, 500, 500], cropUrl: `/crop${id}.png`, imageUrl: `/cell${id}.png` })) } }}
    source={{ frames: [1, 2, 3, 4].map((id) => ({ description: `Shot ${id}` })) }} prepare={prepare} busy={false} conflict={true} />);
  expect(screen.getAllByRole('img')).toHaveLength(4);
  expect(screen.getByText(/20.67/)).toHaveTextContent('18.00');
  fireEvent.change(screen.getByLabelText('镜头 1 描述'), { target: { value: 'New action' } });
  fireEvent.click(screen.getByRole('button', { name: '保留手动编辑并刷新' }));
  expect(prepare).toHaveBeenCalledWith(expect.objectContaining({ resolution: 'keep_edits', shots: expect.arrayContaining([expect.objectContaining({ description: 'New action' })]) }));
});
