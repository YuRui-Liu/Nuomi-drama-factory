import { useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { ImageSplitGateway } from '@/features/canvas/application/ports';
import { webImageSplitGateway } from '@/features/canvas/infrastructure/webImageSplitGateway';
import { loadImageElement } from '@/features/canvas/application/imageData';
import { uploadLocalImageToBackend } from '@/features/canvas/application/uploadToolOutput';
import { getGridCellCropGeometry } from './gridCrop';
import { listTasks, type TaskState } from '@/api/tasks';
import { resolveMediaUrl } from '@/lib/media-url';
import { useCanvasStore } from '@/stores/canvasStore';
import {
  isCurrentGridBinding,
  validateGridBinding,
  type GridCellMapping,
  type GridFrameOrigin,
  type GridFrameRecord,
  type GridRun,
  type GridRunStatus,
  type GroupGridBinding,
} from './gridBinding';

export interface PrepareGridCellsInput {
  sourceUrl: string;
  sourceNodeId: string;
  resultId: string;
  resultRevision: string | number;
  binding: GroupGridBinding;
  rows: number;
  cols: number;
  cellMappings: readonly GridCellMapping[];
  selectedCellIndexes: readonly number[];
  separatorPx?: number;
  /** Prevents delayed crop work from persisting after its canvas or source changes. */
  isCurrent?: () => boolean;
}

export interface PreparedGridCell {
  origin: GridFrameOrigin;
  imageUrl: string;
}

export interface PrepareGridCellsDependencies {
  splitGateway: Pick<ImageSplitGateway, 'split'>;
  readDimensions: (sourceUrl: string) => Promise<{ width: number; height: number }>;
  persistImage?: (imageUrl: string, filename: string) => Promise<string>;
}

const defaultPrepareDependencies: PrepareGridCellsDependencies = {
  splitGateway: webImageSplitGateway,
  readDimensions: async (sourceUrl) => {
    const image = await loadImageElement(sourceUrl);
    return { width: image.naturalWidth, height: image.naturalHeight };
  },
};

export async function prepareSelectedGridCells(
  input: PrepareGridCellsInput,
  dependencies: PrepareGridCellsDependencies = defaultPrepareDependencies,
): Promise<PreparedGridCell[]> {
  const assertCurrent = () => {
    if (input.isCurrent && !input.isCurrent()) {
      throw new Error('宫格来源已更改或画布已切换，已停止拆分');
    }
  };
  const persistImage = dependencies.persistImage
    ?? ((imageUrl: string, filename: string) => uploadLocalImageToBackend(imageUrl, filename, input.binding.project));
  assertCurrent();
  validateGridBinding(input.binding);
  if (!Number.isInteger(input.rows) || input.rows < 1 || !Number.isInteger(input.cols) || input.cols < 1) {
    throw new Error('宫格行数与列数必须是正整数');
  }
  const capacity = input.rows * input.cols;
  const mappingByCell = new Map<number, GridCellMapping>();
  const mappedShotIds = new Set<string>();
  for (const mapping of input.cellMappings) {
    if (!Number.isInteger(mapping.cellIndex) || mapping.cellIndex < 0 || mapping.cellIndex >= capacity) {
      throw new Error('宫格镜头映射超出有效格位');
    }
    if (mappingByCell.has(mapping.cellIndex)) throw new Error('宫格镜头映射包含重复格位');
    if (mapping.shotId !== null && mappedShotIds.has(mapping.shotId)) throw new Error('宫格镜头映射包含重复镜头');
    if (mapping.shotId !== null) mappedShotIds.add(mapping.shotId);
    mappingByCell.set(mapping.cellIndex, mapping);
  }
  const selected = [...new Set(input.selectedCellIndexes)].sort((a, b) => a - b);
  if (!selected.length) throw new Error('请先选择要拆分的镜头格');
  for (const index of selected) {
    if (!Number.isInteger(index) || index < 0 || index >= capacity) throw new Error('选择的宫格位置无效');
  }
  const mappedSelected = selected.filter((index) => Boolean(mappingByCell.get(index)?.shotId));
  if (!mappedSelected.length) throw new Error('所选位置没有有效镜头，不能拆分空白格');

  const { width, height } = await dependencies.readDimensions(input.sourceUrl);
  assertCurrent();
  if (!Number.isFinite(width) || !Number.isFinite(height) || width < input.cols || height < input.rows) {
    throw new Error('源宫格尺寸不足，无法按布局拆分');
  }
  const separatorPx = input.separatorPx ?? 0;
  const geometry = getGridCellCropGeometry(width, height, input.rows, input.cols, separatorPx);
  const split = await dependencies.splitGateway.split(input.sourceUrl, input.rows, input.cols, separatorPx);
  assertCurrent();
  if (split.length < capacity) throw new Error('宫格拆分服务未返回所有格位');

  const prepared: PreparedGridCell[] = [];
  for (const cellIndex of mappedSelected) {
    assertCurrent();
    const mapping = mappingByCell.get(cellIndex)!;
    const crop = geometry[cellIndex]?.crop;
    if (!crop) throw new Error(`第 ${cellIndex + 1} 格缺少有效的裁切几何`);
    if (crop.x < 0 || crop.y < 0 || crop.width < 1 || crop.height < 1
      || crop.x + crop.width > width || crop.y + crop.height > height) {
      throw new Error(`第 ${cellIndex + 1} 格的裁切范围超出源图片尺寸`);
    }
    const imageUrl = await persistImage(
      split[cellIndex],
      `grid-${input.resultId.replace(/[^\w.-]/g, '_')}-cell-${cellIndex + 1}.png`,
    );
    assertCurrent();
    prepared.push({
      origin: {
        gridNodeId: input.sourceNodeId,
        gridResultId: input.resultId,
        gridResultRevision: input.resultRevision,
        binding: input.binding,
        cellIndex,
        shotId: mapping.shotId!,
        crop,
      },
      imageUrl,
    });
  }
  return prepared;
}

interface GridTaskResult {
  status?: string;
  revision?: number | string;
  result_revision?: number | string;
  batch_id?: string;
  grid_url?: string;
  grid_asset?: string;
  error?: string;
  batches?: GridTaskResult[];
  errors?: Array<string | { message?: string }>;
}

function resultForRun(task: TaskState, run: GridRun, runs: readonly GridRun[]): GridTaskResult | null {
  const result = task.result as GridTaskResult | null | undefined;
  if (!result) return null;
  const batches = Array.isArray(result.batches) ? result.batches : [];
  if (!batches.length) {
    return typeof result.batch_id === 'string' && result.batch_id.length > 0 && result.batch_id !== run.batchId
      ? null
      : result;
  }
  const exact = batches.find((batch) => batch.batch_id === run.batchId);
  if (exact) return exact;
  // Once the server provides batch identity, never attach a sibling's image to
  // this run. Positional compatibility is only for responses without ids.
  if (batches.some((batch) => typeof batch.batch_id === 'string' && batch.batch_id.length > 0)) return null;
  if (batches.length === 1) return batches[0];
  // The runner returns one result per submitted generation batch in the same
  // order as the frozen plan. Older runner responses omit the batch_id, so use
  // the matching task's stable persisted run order as the fallback mapping.
  const siblings = runs.filter((candidate) => candidate.taskKey === run.taskKey);
  const index = siblings.findIndex((candidate) => candidate.runId === run.runId);
  return batches.length === siblings.length && index >= 0 ? batches[index] : null;
}

function gridStatus(status: TaskState['status']): GridRunStatus {
  if (status === 'completed') return 'completed';
  if (status === 'failed') return 'failed';
  if (status === 'cancelled') return 'cancelled';
  if (status === 'running') return 'running';
  return 'queued';
}

export function restoreGridRunFromTask(
  task: TaskState,
  run: GridRun,
  runs: readonly GridRun[],
): GridRun | null {
  const batch = resultForRun(task, run, runs);
  const taskStatus = task.status === 'completed' && batch?.status === 'failed'
    ? 'failed'
    : gridStatus(task.status);
  const taskResult = task.result as GridTaskResult | null | undefined;
  const resultBatches = Array.isArray(taskResult?.batches) ? taskResult.batches : [];
  const imagePath = batch?.grid_url ?? batch?.grid_asset;
  const imageUrl = imagePath ? resolveMediaUrl(imagePath) : null;
  const completedWithoutImage = taskStatus === 'completed' && !imageUrl;
  const mismatchedBatch = Boolean(
    taskStatus === 'completed'
    && !batch
    && resultBatches.some((item) => typeof item.batch_id === 'string' && item.batch_id.length > 0),
  );
  const batchErrors = batch?.errors?.map((item) => typeof item === 'string' ? item : item.message ?? '')
    .filter(Boolean).join('；');
  const error = mismatchedBatch
    ? '生成任务完成，但没有返回此宫格批次的结果'
    : task.error ?? batch?.error ?? batchErrors
      ?? (completedWithoutImage ? '生成任务完成但没有返回可访问的宫格图片' : run.error);
  return {
    ...run,
    status: completedWithoutImage || mismatchedBatch ? 'failed' : taskStatus,
    imageUrl: imageUrl ?? run.imageUrl,
    resultRevision: batch?.result_revision ?? batch?.revision ?? run.resultRevision,
    error: taskStatus === 'failed' || completedWithoutImage || mismatchedBatch ? error : null,
    updatedAt: task.updated_at ?? run.updatedAt,
  };
}

function sameFrameSource(left: GridFrameOrigin | undefined, right: GridFrameOrigin | undefined): boolean {
  return Boolean(left && right
    && left.gridNodeId === right.gridNodeId
    && left.gridResultId === right.gridResultId
    && left.gridResultRevision === right.gridResultRevision
    && left.cellIndex === right.cellIndex
    && left.shotId === right.shotId
    && JSON.stringify([
      left.binding.project, left.binding.episode, left.binding.groupId, left.binding.directorRevisionId,
    ]) === JSON.stringify([
      right.binding.project, right.binding.episode, right.binding.groupId, right.binding.directorRevisionId,
    ]));
}

/** Restores grid and HD task progress from the durable task registry after reload. */
export function useGridTaskRecovery(nodeId: string, binding: GroupGridBinding) {
  const nodes = useCanvasStore((state) => state.nodes);
  const node = nodes.find((item) => item.id === nodeId);
  const snapshotBinding = (node?.data as { gridBinding?: GroupGridBinding } | undefined)?.gridBinding;
  const runs = (node?.data as { gridRuns?: GridRun[] } | undefined)?.gridRuns ?? EMPTY_GRID_RUNS;
  const frames = useMemo(
    () => nodes.filter((item) => (
      item.data as unknown as GridRecoveryFrameNode['data']
    ).gridFrameOrigin?.gridNodeId === nodeId) as unknown as GridRecoveryFrameNode[],
    [nodeId, nodes],
  );
  const activeKeys = [
    ...runs.filter((run) => run.status === 'queued' || run.status === 'running').map((run) => run.taskKey),
    ...frames.flatMap((frame) => (frame.data.gridFrameRecord?.candidates ?? [])
      .filter((candidate) => candidate.status === 'queued' || candidate.status === 'running')
      .map((candidate) => candidate.taskKey ?? '')),
  ].filter(Boolean);
  const activeKey = [...new Set(activeKeys)].sort().join(',');
  const tasks = useQuery({
    queryKey: ['canvas-grid-task-recovery', binding.project, nodeId, activeKey],
    queryFn: () => listTasks(binding.project),
    enabled: Boolean(binding.project && activeKey),
    refetchInterval: activeKey ? 2_000 : false,
  });

  useEffect(() => {
    if (!tasks.data || !isCurrentGridBinding(snapshotBinding, binding)) return;
    const taskByKey = new Map(tasks.data.map((task) => [task.task_key, task] as const));
    const state = useCanvasStore.getState();
    const latest = state.nodes.find((item) => item.id === nodeId);
    const latestData = (latest?.data ?? {}) as { gridBinding?: GroupGridBinding; gridRuns?: GridRun[] };
    if (!isCurrentGridBinding(latestData.gridBinding, binding)) return;
    const nextRuns = (latestData.gridRuns ?? []).map((run) => {
      const task = taskByKey.get(run.taskKey);
      if (!task) return run;
      return restoreGridRunFromTask(task, run, latestData.gridRuns ?? []) ?? run;
    });
    if (JSON.stringify(nextRuns) !== JSON.stringify(latestData.gridRuns ?? [])) {
      state.updateNodeData(nodeId, { gridRuns: nextRuns });
    }

    for (const frameNode of frames) {
      const latestFrame = state.nodes.find((item) => item.id === frameNode.id);
      const record = latestFrame?.data.gridFrameRecord as GridFrameRecord | undefined;
      const expectedOrigin = frameNode.data.gridFrameOrigin as GridFrameOrigin | undefined;
      if (!record || !sameFrameSource(latestFrame?.data.gridFrameOrigin as GridFrameOrigin | undefined, expectedOrigin)) continue;
      let changed = false;
      const candidates = record.candidates.map((candidate) => {
        if (!candidate.taskKey) return candidate;
        const task = taskByKey.get(candidate.taskKey);
        if (!task) return candidate;
        const raw = task.result as Record<string, unknown> | null | undefined;
        const imagePath = [raw?.output_url, raw?.image_url, raw?.url, raw?.result_url]
          .find((value): value is string => typeof value === 'string' && value.length > 0);
        const status = gridStatus(task.status);
        const next = {
          ...candidate,
          status: status === 'completed' && !imagePath ? 'failed' : status,
          imageUrl: imagePath ? resolveMediaUrl(imagePath) : candidate.imageUrl,
          error: task.error ?? (status === 'completed' && !imagePath ? '高清任务完成但没有返回图片' : null),
          updatedAt: task.updated_at ?? candidate.updatedAt,
        };
        if (JSON.stringify(next) !== JSON.stringify(candidate)) changed = true;
        return next;
      });
      if (changed) state.updateNodeData(frameNode.id, { gridFrameRecord: { ...record, candidates } });
    }
  }, [binding, nodeId, snapshotBinding, frames, runs, tasks.data]);

  return { error: tasks.error, isFetching: tasks.isFetching };
}

const EMPTY_GRID_RUNS: GridRun[] = [];

interface GridRecoveryFrameNode {
  id: string;
  data: {
    gridFrameOrigin?: GridFrameOrigin;
    gridFrameRecord?: GridFrameRecord;
  };
}
