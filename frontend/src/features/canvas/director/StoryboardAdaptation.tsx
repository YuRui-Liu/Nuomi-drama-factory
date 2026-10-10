import { useEffect, useState } from 'react';
import type { VideoDirectorNodeData } from '../domain/canvasNodes';
import type { StoryboardPreparationOptions } from '@/api/videoDirector';
import { resolveMediaUrl } from '@/lib/media-url';
import { isStoryboardPreparationStale } from '../domain/videoDirectorBindings';

type Shot = { id?: string; referenceIndex?: number | null; description: string; duration_seconds?: number };
export function StoryboardAdaptation({ data, source, sourceNodeId, prepare, busy, conflict, onSourceChange, referenceError }: {
  data: VideoDirectorNodeData; sourceNodeId?: string;
  source?: { frames?: Shot[]; imageUrl?: string | null; gridRows?: number; gridCols?: number; cellRects?: [number, number, number, number][] };
  prepare: (options: StoryboardPreparationOptions) => unknown; busy: boolean; conflict: boolean;
  referenceError?: string;
  onSourceChange?: (patch: { frames?: Shot[]; cellRects?: [number, number, number, number][] }) => void;
}) {
  const prepared = data.storyboardPreparation;
  const mode = data.inputModeIntent === 'manual' && data.activeInputMode === 'frames' ? 'frames' : 'ref';
  const count = source?.gridRows !== undefined && source?.gridCols !== undefined
    ? source.gridRows * source.gridCols : prepared?.cells.length ?? 4;
  const stale = isStoryboardPreparationStale(prepared, { ...source, id: sourceNodeId }) ||
    !!prepared && (prepared.inputMode !== mode || !!referenceError);
  const initialRects = () => stale ? undefined : source?.cellRects ?? prepared?.cells.map((cell) => cell.rect);
  const initialShots = () => Array.from({ length: count }, (_, i) => ({
    id: source?.frames?.[i]?.id ?? `${prepared?.sourceNodeId ?? data.autoStoryboard?.sourceNodeId ?? 'board'}-shot-${i + 1}`,
    referenceIndex: source?.frames?.[i]?.referenceIndex ?? null,
    description: source?.frames?.[i]?.description ?? '', duration_seconds: source?.frames?.[i]?.duration_seconds,
  }));
  const [shots, setShots] = useState<Shot[]>(initialShots);
  const [rects, setRects] = useState(initialRects);
  useEffect(() => { setShots(initialShots()); setRects(initialRects()); }, [prepared, source, sourceNodeId]);
  const run = (resolution: StoryboardPreparationOptions['resolution']) => prepare({ resolution, shots, ...(!stale && rects ? { rects } : {}) });
  const patchShot = (index: number, patch: Partial<Shot>) => {
    const next = shots.map((item, i) => i === index ? { ...item, ...patch } : item);
    setShots(next); onSourceChange?.({ frames: next });
  };
  return <fieldset disabled={busy} className="mb-4 rounded border border-cyan-700/40 p-3 text-sm">
    <legend>分镜自动适配 · 每格一个镜头</legend>
    <p>{mode === 'ref' ? 'Ref 自动适配：完整分镜图作为 Picture 1，结合角色与场景参考图，逐格描述生成多段视频。拆格仅作预览，不提交首尾帧。'
      : '逐格拆图并绑定首帧，整组生成一个多段视频。Frames 模式不使用已连接的 Ref 图。'}</p>
    {stale && <p role="alert" className="text-amber-300">分镜来源已变化或输入模式已切换，请重新准备。旧预览、裁剪边界和时长已失效。{referenceError && '参考图缺失或已变化，请检查连线。'}</p>}
    {prepared && !stale && <><p>请求 {prepared.requestedDurationSeconds.toFixed(2)} 秒 · H3 实际 {prepared.actualDurationSeconds.toFixed(2)} 秒（{prepared.actualTotalFrames} 帧）</p>
      <p>源图 {prepared.sourceWidth} × {prepared.sourceHeight}；裁剪边界为像素 x0、y0、x1、y1。</p></>}
    <div className="grid grid-cols-2 gap-3">{shots.map((shot, i) => <div key={i} className="rounded border border-white/10 p-2">
      {!stale && prepared?.cells[i] && <img className="h-32 w-full object-contain" src={resolveMediaUrl(prepared.cells[i].imageUrl) ?? undefined} alt={`镜头 ${i + 1} ${mode === 'ref' ? '单格预览' : '单格首帧'}`} />}
      <label>镜头 {i + 1} 描述<textarea aria-label={`镜头 ${i + 1} 描述`} className="block w-full bg-black/20" value={shot.description}
        onChange={(event) => patchShot(i, { description: event.target.value })} /></label>
      {!shot.description.trim() && <p className="text-amber-300">请填写这个镜头的独立动作描述</p>}
      <label>时长（秒，可留空自动分配）<input type="number" min="0.1" step="0.1" aria-label={`镜头 ${i + 1} 时长`} value={shot.duration_seconds ?? ''}
        onChange={(event) => patchShot(i, { duration_seconds: event.target.value ? Number(event.target.value) : undefined })} /></label>
      {!stale && rects?.[i] && <div className="flex gap-1">{rects[i].map((value, coordinate) => <input key={coordinate} className="w-1/4 bg-black/20" type="number" min="0"
        aria-label={`镜头 ${i + 1} ${['x0', 'y0', 'x1', 'y1'][coordinate]}`} value={value}
        onChange={(event) => { const next = rects.map((rect, j) => j === i ? rect.map((v, k) => k === coordinate ? Number(event.target.value) : v) as typeof rect : rect);
          setRects(next); onSourceChange?.({ cellRects: next }); }} />)}</div>}
    </div>)}</div>
    <button type="button" onClick={() => run('safe')}>{busy ? '正在准备…' : mode === 'ref' ? '准备 / 刷新分镜参考' : '准备 / 刷新分镜首帧'}</button>
    {conflict && <div role="alert"><p>分镜来源已变化，现有分段包含手动编辑，请选择处理方式。</p>
      <button type="button" onClick={() => run('keep_edits')}>保留手动编辑并刷新</button>
      <button type="button" onClick={() => run('replace_generated')}>重新生成并替换分段</button></div>}
  </fieldset>;
}
