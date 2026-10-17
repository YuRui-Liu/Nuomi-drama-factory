import { describe, expect, it, vi } from 'vitest';
import {
  prepareSelectedGridCells,
  restoreGridRunFromTask,
  type PrepareGridCellsDependencies,
} from '@/features/canvas/production/grid/useGridExtraction';
import type { GroupGridBinding, GridCellMapping } from '@/features/canvas/production/grid/gridBinding';

const binding: GroupGridBinding = {
  project: 'project-a', episode: 2, groupId: 'group-a', directorRevisionId: 'director-r1',
  shotIds: ['shot-1', 'shot-2', 'shot-3'],
};
const mappings: GridCellMapping[] = [
  { cellIndex: 0, shotId: 'shot-1' },
  { cellIndex: 1, shotId: 'shot-2' },
  { cellIndex: 2, shotId: 'shot-3' },
  { cellIndex: 3, shotId: null },
];

describe('direct grid extraction', () => {
  it('uses the split gateway, crops selected mapped cells in row-major order, and records exact pixels', async () => {
    const split = vi.fn(async () => ['cell-0', 'cell-1', 'cell-2', 'cell-3']);
    const persist = vi.fn(async (image: string) => `stored:${image}`);
    const dependencies: PrepareGridCellsDependencies = {
      splitGateway: { split },
      readDimensions: async () => ({ width: 101, height: 53 }),
      persistImage: persist,
    };

    const frames = await prepareSelectedGridCells({
      sourceUrl: 'grid.png',
      sourceNodeId: 'grid-node',
      resultId: 'result-r1',
      resultRevision: 7,
      binding,
      rows: 2,
      cols: 2,
      cellMappings: mappings,
      selectedCellIndexes: [3, 1, 2],
      separatorPx: 1,
    }, dependencies);

    expect(split).toHaveBeenCalledExactlyOnceWith('grid.png', 2, 2, 1);
    expect(frames.map((frame) => frame.origin.cellIndex)).toEqual([1, 2]);
    expect(frames.map((frame) => frame.origin.shotId)).toEqual(['shot-2', 'shot-3']);
    expect(frames.map((frame) => frame.origin.crop)).toEqual([
      { x: 51, y: 0, width: 50, height: 26 },
      { x: 0, y: 27, width: 50, height: 26 },
    ]);
    expect(frames.map((frame) => frame.imageUrl)).toEqual(['stored:cell-1', 'stored:cell-2']);
    expect(persist).toHaveBeenCalledTimes(2);
  });

  it('rejects a selected cell whose crop falls outside the source image', async () => {
    await expect(prepareSelectedGridCells({
      sourceUrl: 'grid.png', sourceNodeId: 'grid-node', resultId: 'result-r1', resultRevision: 7,
      binding, rows: 2, cols: 2, cellMappings: mappings, selectedCellIndexes: [0],
    }, {
      splitGateway: { split: async () => ['cell-0', 'cell-1', 'cell-2', 'cell-3'] },
      readDimensions: async () => ({ width: 1, height: 1 }),
      persistImage: async (image) => image,
    })).rejects.toThrow(/尺寸不足|裁切范围/);
  });

  it('stops persisting cells when the captured async source becomes stale', async () => {
    let isCurrent = true;
    const persist = vi.fn(async (image: string) => image);
    await expect(prepareSelectedGridCells({
      sourceUrl: 'grid.png', sourceNodeId: 'grid-node', resultId: 'result-r1', resultRevision: 7,
      binding, rows: 2, cols: 2, cellMappings: mappings, selectedCellIndexes: [0],
      isCurrent: () => isCurrent,
    }, {
      splitGateway: { split: async () => { isCurrent = false; return ['cell-0', 'cell-1', 'cell-2', 'cell-3']; } },
      readDimensions: async () => ({ width: 101, height: 53 }),
      persistImage: persist,
    })).rejects.toThrow(/来源已更改|画布已切换/);
    expect(persist).not.toHaveBeenCalled();
  });

  it('restores the result revision and image for the exact persisted batch id', () => {
    const run = {
      runId: 'task:grid-b', batchId: 'grid-b', taskKey: 'task', taskScope: 'scope',
      status: 'queued' as const, resultId: 'task:grid-b', resultRevision: 2, imageUrl: null,
      rows: 1, cols: 1, cellMappings: [{ cellIndex: 0, shotId: 'shot-2' }],
    };
    const restored = restoreGridRunFromTask({
      task_type: 'narrative_group_grid', task_key: 'task', username: 'user', project: 'project-a',
      episode: 2, status: 'completed', result: { batches: [
        { batch_id: 'grid-a', revision: 3, grid_url: '/grid-a.png' },
        { batch_id: 'grid-b', revision: 4, grid_url: '/grid-b.png' },
      ] },
    }, run, [run]);

    expect(restored).toMatchObject({ status: 'completed', resultRevision: 4, imageUrl: '/grid-b.png' });
  });

  it('does not attach a different batch result when the response has batch ids', () => {
    const run = {
      runId: 'task:grid-b', batchId: 'grid-b', taskKey: 'task', taskScope: 'scope',
      status: 'queued' as const, resultId: 'task:grid-b', resultRevision: 2, imageUrl: null,
      rows: 1, cols: 1, cellMappings: [{ cellIndex: 0, shotId: 'shot-2' }],
    };
    const restored = restoreGridRunFromTask({
      task_type: 'narrative_group_grid', task_key: 'task', username: 'user', project: 'project-a',
      episode: 2, status: 'completed', result: { batches: [
        { batch_id: 'grid-a', revision: 3, grid_url: '/grid-a.png' },
      ] },
    }, run, [run]);

    expect(restored).toMatchObject({ status: 'failed', imageUrl: null, resultRevision: 2 });
  });
});
