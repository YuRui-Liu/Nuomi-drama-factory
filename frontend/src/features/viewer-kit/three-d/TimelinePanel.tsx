import { useEffect, useRef, useState } from 'react';
import type { ThreeDSceneSnapshot, ViewerApp } from './engine/viewerApp';
import { POSES, POSE_LABELS, type PoseName } from './engine/poses';
import { emptyTimeline, pathKeys, upsertKey, type SceneTimeline, type Vec3 } from './sceneTimeline';
import { recordTimeline, supportedRecordingMime } from './recordTimeline';
export type PrevizExport = (blob: Blob, snapshot: ThreeDSceneSnapshot, size: { width: number; height: number }) => Promise<void>;
export function TimelinePanel({ viewer, rotationOnly, onExport }: { viewer: ViewerApp; rotationOnly: boolean; onExport?: PrevizExport }) {
  const [timeline, setTimeline] = useState<SceneTimeline>(() => viewer.getTimeline?.() ?? emptyTimeline());
  const [time, setTime] = useState(0), [playing, setPlaying] = useState(false), [error, setError] = useState('');
  const [pose, setPose] = useState<PoseName>('walking'), [speed, setSpeed] = useState(1), [progress, setProgress] = useState<number | null>(null);
  const [end, setEnd] = useState(timeline.duration), [fov, setFov] = useState(viewer.camera?.camera?.fov ?? 60);
  const controller = useRef<AbortController | null>(null), points = useRef<Vec3[]>([]);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => viewer.onMarkersChange(() => { setTimeline(viewer.getTimeline()); setPlaying(false); setTime(0); }), [viewer]);
  useEffect(() => {
    if (!playing) return;
    let frame = 0; const start = performance.now() - time * 1000;
    const tick = (now: number) => { const t = Math.min(timeline.duration, (now - start) / 1000); viewer.seekTimeline(t, rotationOnly); setTime(t); if (t >= timeline.duration) setPlaying(false); else frame = requestAnimationFrame(tick); };
    frame = requestAnimationFrame(tick); return () => cancelAnimationFrame(frame);
  // The start time is fixed for this playback run.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, timeline.duration, viewer, rotationOnly]);
  const change = (next: SceneTimeline) => { try { viewer.setTimeline(next); setTimeline(next); setError(''); } catch (e) { setError(String(e)); } };
  const selected = () => {
    const selection = viewer.getSelection(); if (!selection) throw new Error('请先选择角色或物体');
    const snapshot = viewer.exportSceneSnapshot();
    const item = (selection.kind === 'actor' ? snapshot.actors : selection.kind === 'prop' ? snapshot.props : snapshot.stagings)[selection.index];
    if (!item?.id) throw new Error('物体没有稳定标识');
    return { item, selection, track: timeline.tracks[item.id] ?? { keys: [], clips: [] } };
  };
  const safely = (fn: () => void) => { try { fn(); setError(''); } catch (e) { setError(String(e)); } };
  const seek = (t: number) => { setPlaying(false); setTime(t); viewer.seekTimeline(t, rotationOnly); };
  const exportVideo = async () => {
    setPlaying(false); setProgress(0); controller.current = new AbortController();
    const gizmoEnabled = viewer.isTranslateGizmoEnabled();
    viewer.setTranslateGizmoEnabled(false);
    viewer.fly.setInputEnabled(false);
    const restore = time;
    try { const blob = await recordTimeline(viewer, controller.current.signal, setProgress, rotationOnly); if (!controller.current.signal.aborted) { const canvas = viewer.app.graphicsDevice.canvas; await onExport?.(blob, viewer.exportSceneSnapshot(), { width: canvas.width, height: canvas.height }); } }
    catch (e) { if (!(e instanceof DOMException && e.name === 'AbortError')) setError(String(e)); }
    finally { viewer.seekTimeline(restore, rotationOnly); viewer.setTranslateGizmoEnabled(gizmoEnabled); viewer.fly.setInputEnabled(true); setProgress(null); controller.current = null; }
  };
  return <section className="absolute bottom-3 left-3 right-3 z-20 max-h-[45%] overflow-auto rounded-xl bg-black/90 p-3 text-xs text-white" aria-label="动态预演时间轴" onKeyDown={e => e.stopPropagation()}>
    <fieldset disabled={progress !== null} className="space-y-2">
      <div className="flex flex-wrap items-center gap-3"><strong>动态预演</strong><button onClick={() => { if (time >= timeline.duration) seek(0); setPlaying(!playing); }}>{playing ? '暂停' : '播放'}</button><span>{time.toFixed(2)} 秒</span><label>总时长 <input className="w-16 bg-white/10" type="number" min={0.1} max={120} step={0.1} value={timeline.duration} onChange={e => change({ ...timeline, duration: Number(e.target.value) })} /></label></div>
      <input aria-label="时间游标" type="range" className="w-full" min={0} max={timeline.duration} step={0.01} value={time} onChange={e => seek(Number(e.target.value))} />
      <div className="flex flex-wrap gap-3">
        <button onClick={() => safely(() => { const { item, track } = selected(); change({ ...timeline, tracks: { ...timeline.tracks, [item.id!]: { ...track, keys: upsertKey(track.keys, { time, position: item.position, yawDeg: item.yawDeg, scale: item.scale }) } } }); })}>记录物体关键帧</button>
        <button onClick={() => safely(() => { const { item, track } = selected(); change({ ...timeline, tracks: { ...timeline.tracks, [item.id!]: { ...track, keys: track.keys.filter(k => Math.abs(k.time - time) > 0.005) } } }); })}>删除当前物体帧</button>
        <label>视场角 <input className="w-14 bg-white/10" type="number" min={10} max={120} value={fov} onChange={e => { setFov(Number(e.target.value)); if (viewer.camera.camera) viewer.camera.camera.fov = Number(e.target.value); }} /></label>
        <button onClick={() => change({ ...timeline, camera: upsertKey(timeline.camera, { ...viewer.fly.getCameraState(), time, fov }) })}>记录机位关键帧</button>
        <button onClick={() => change({ ...timeline, camera: timeline.camera.filter(k => Math.abs(k.time - time) > 0.005) })}>删除当前机位帧</button>
      </div>
      <div className="flex flex-wrap gap-3"><label>动作库 <select className="bg-zinc-800" value={pose} onChange={e => setPose(e.target.value as PoseName)}>{POSES.map(p => <option key={p} value={p}>{POSE_LABELS[p]}</option>)}</select></label><label>片段/路径结束秒 <input className="w-14 bg-white/10" type="number" step={0.1} value={end} onChange={e => setEnd(Number(e.target.value))} /></label><label>动作速度 <input className="w-14 bg-white/10" type="number" step={0.1} value={speed} onChange={e => setSpeed(Number(e.target.value))} /></label>
        <button onClick={() => safely(() => { const { item, selection, track } = selected(); if (selection.kind !== 'actor') throw new Error('动作仅适用于角色'); change({ ...timeline, tracks: { ...timeline.tracks, [item.id!]: { ...track, clips: [...track.clips, { start: time, end, pose, speed }].sort((a, b) => a.start - b.start) } } }); })}>添加动作片段</button>
        <button onClick={() => safely(() => { const { item, track } = selected(); change({ ...timeline, tracks: { ...timeline.tracks, [item.id!]: { ...track, clips: track.clips.filter(c => !(time >= c.start && time < c.end)) } } }); })}>删除当前动作</button>
      </div>
      <details><summary>手绘运动路径（俯视 X/Z，20 米范围；结束时间控制速度）</summary><svg viewBox="0 0 200 100" className="h-28 w-full touch-none bg-white/10" onPointerDown={e => { points.current = []; e.currentTarget.setPointerCapture(e.pointerId); }} onPointerMove={e => { if (!e.buttons) return; const rect = e.currentTarget.getBoundingClientRect(); safely(() => { const { item } = selected(); points.current.push([(e.clientX - rect.left) / rect.width * 20 - 10, item.position[1], (e.clientY - rect.top) / rect.height * 20 - 10]); }); }} onPointerUp={() => safely(() => { const { item, track } = selected(); const keys = pathKeys(points.current, time, end, { time, position: item.position, yawDeg: item.yawDeg, scale: item.scale }); change({ ...timeline, tracks: { ...timeline.tracks, [item.id!]: { ...track, keys: [...track.keys.filter(k => k.time < time || k.time > end), ...keys].sort((a, b) => a.time - b.time) } } }); })}>
        {Object.values(timeline.tracks).map((track, i) => <polyline key={i} points={track.keys.map(k => `${(k.position[0] + 10) * 10},${(k.position[2] + 10) * 5}`).join(' ')} fill="none" stroke="#facc15" strokeWidth="1" />)}
      </svg></details>
      <details><summary>关键帧与片段（点击时间定位，再摆位记录以修改）</summary>{Object.entries(timeline.tracks).map(([id, track]) => <div key={id}>{id.slice(0, 8)} · {track.keys.map(k => <button className="mx-1" key={k.time} onClick={() => seek(k.time)}>{k.time.toFixed(2)}◆</button>)}{track.clips.map(c => <span key={c.start}> {POSE_LABELS[c.pose]} {c.start}–{c.end}s</span>)}</div>)}<div>机位 {timeline.camera.map(k => <button className="mx-1" key={k.time} onClick={() => seek(k.time)}>{k.time.toFixed(2)}◆</button>)}</div></details>
      {rotationOnly && <p>全景背景仅支持机位旋转及视场角，不作空间漫游。</p>}
      {onExport && <button onClick={() => void exportVideo()}>导出动态参考到画布（30 fps）</button>}{!supportedRecordingMime() && <p>浏览器不支持实时编码，将使用本地 FFmpeg 逐帧编码，可能需要较长时间。</p>}
    </fieldset>
    {progress !== null && <div className="fixed inset-0 z-[100] flex items-center justify-center gap-4 bg-black/50" onPointerDown={e => e.stopPropagation()}>录制 {Math.round(progress * 100)}% <button className="rounded bg-zinc-700 p-3" onClick={() => controller.current?.abort()}>取消</button></div>}{error && <p role="alert" className="text-red-300">{error}</p>}
  </section>;
}
