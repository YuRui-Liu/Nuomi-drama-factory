export interface GroupGridBinding {
  project: string;
  episode: number;
  groupId: string;
  directorRevisionId: string;
  shotIds: string[];
  /** Frozen director descriptions used by optional per-frame HD extraction. */
  shotDescriptions?: Record<string, string>;
}

export interface GridCellMapping {
  /** Zero-based row-major position in the source grid. */
  cellIndex: number;
  /** `null` denotes a deliberately empty cell. */
  shotId: string | null;
}

export type GridRunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

/** One result image from a narrative group render task, tied to its real plan batch. */
export interface GridRun {
  runId: string;
  batchId: string;
  taskKey: string;
  taskScope: string;
  status: GridRunStatus;
  resultId: string;
  resultRevision: string | number;
  imageUrl: string | null;
  rows: number;
  cols: number;
  cellMappings: GridCellMapping[];
  error?: string | null;
  updatedAt?: string;
}

export interface GridFrameCrop {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface GridFrameOrigin {
  gridNodeId: string;
  gridResultId: string;
  gridResultRevision: string | number;
  binding: GroupGridBinding;
  cellIndex: number;
  shotId: string;
  crop: GridFrameCrop;
}

export type GridHdStatus = 'submitting' | GridRunStatus;

export interface GridHdCandidate {
  candidateId: string;
  taskKey?: string;
  status: GridHdStatus;
  model: string;
  size: string;
  imageUrl?: string | null;
  error?: string | null;
  updatedAt?: string;
}

export interface GridFrameRecord {
  origin: GridFrameOrigin;
  cropUrl: string;
  candidates: GridHdCandidate[];
  selectedCandidateId?: string | null;
}

export function gridBindingKey(binding: GroupGridBinding): string {
  return JSON.stringify([
    binding.project,
    binding.episode,
    binding.groupId,
    binding.directorRevisionId,
  ]);
}

/** Check a captured async request scope before it writes back to a canvas node. */
export function isCurrentGridBinding(
  current: GroupGridBinding | null | undefined,
  expected: GroupGridBinding,
): boolean {
  return Boolean(current && gridBindingKey(current) === gridBindingKey(expected));
}

/** Guards async frame extraction against selecting a newer result on the same node. */
export function isCurrentGridRun(
  currentBinding: GroupGridBinding | null | undefined,
  expectedBinding: GroupGridBinding,
  currentRun: GridRun | null | undefined,
  expectedRun: GridRun,
): boolean {
  return isCurrentGridBinding(currentBinding, expectedBinding)
    && Boolean(currentRun
      && currentRun.runId === expectedRun.runId
      && currentRun.resultId === expectedRun.resultId
      && currentRun.resultRevision === expectedRun.resultRevision
      && currentRun.imageUrl === expectedRun.imageUrl);
}

export function validateGridBinding(binding: GroupGridBinding): void {
  if (!binding.project.trim()) throw new Error('项目绑定不能为空');
  if (!Number.isInteger(binding.episode) || binding.episode <= 0) throw new Error('集数绑定无效');
  if (!binding.groupId.trim()) throw new Error('叙事组绑定不能为空');
  if (!binding.directorRevisionId.trim()) throw new Error('导演版本绑定不能为空');
  if (binding.shotIds.some((shotId) => !shotId.trim())) throw new Error('镜头编号不能为空');
  if (new Set(binding.shotIds).size !== binding.shotIds.length) throw new Error('镜头编号不能重复');
}

export function buildGridCellMappings(
  shotIds: readonly string[],
  rows: number,
  cols: number,
): GridCellMapping[] {
  if (!Number.isInteger(rows) || rows < 1 || !Number.isInteger(cols) || cols < 1) {
    throw new Error('宫格行数与列数必须是正整数');
  }
  if (shotIds.some((shotId) => !shotId.trim())) throw new Error('镜头编号不能为空');
  if (new Set(shotIds).size !== shotIds.length) throw new Error('duplicate shot id');
  const capacity = rows * cols;
  if (capacity < shotIds.length) throw new Error(`grid capacity ${capacity} is smaller than ${shotIds.length} shots`);
  return Array.from({ length: capacity }, (_, cellIndex) => ({
    cellIndex,
    shotId: shotIds[cellIndex] ?? null,
  }));
}

export function findGridRunForCell(runs: readonly GridRun[], resultId: string, cellIndex: number) {
  const run = runs.find((item) => item.resultId === resultId);
  return run?.cellMappings.find((item) => item.cellIndex === cellIndex) ?? null;
}
