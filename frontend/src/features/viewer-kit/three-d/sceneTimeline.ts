import { POSES, type PoseName } from './engine/poses';
export type Vec3 = [number, number, number];
export interface ObjectKey { time: number; position: Vec3; yawDeg: number; scale: Vec3 }
export interface CameraKey { time: number; azim: number; elev: number; distance: number; focalPoint: Vec3; fov: number }
export interface ActionClip { start: number; end: number; pose: PoseName; speed: number }
export interface SceneTimeline { version: 1; duration: number; tracks: Record<string, { keys: ObjectKey[]; clips: ActionClip[] }>; camera: CameraKey[] }
export const emptyTimeline = (): SceneTimeline => ({ version: 1, duration: 5, tracks: {}, camera: [] });
const finite = (n: number) => Number.isFinite(n);
export function validateTimeline(t: SceneTimeline): void {
  if (t.version !== 1 || !finite(t.duration) || t.duration <= 0 || t.duration > 120) throw new Error('预演时长须在 0–120 秒内');
  const keys = (rows: { time: number }[]) => {
    let last = -1;
    for (const k of rows) {
      if (!finite(k.time) || k.time < 0 || k.time > t.duration || k.time <= last) throw new Error('关键帧时间必须严格递增且在时长内');
      last = k.time;
      for (const value of Object.values(k as Record<string, unknown>)) if (typeof value === 'number' ? !finite(value) : Array.isArray(value) && (value.length !== 3 || !value.every(finite))) throw new Error('关键帧包含无效数值');
    }
  };
  keys(t.camera);
  if (t.camera.some(k => k.fov < 10 || k.fov > 120 || k.distance <= 0)) throw new Error('机位视场角须为 10–120 度');
  for (const [id, track] of Object.entries(t.tracks)) {
    if (!id) throw new Error('物体缺少稳定标识');
    keys(track.keys);
    let end = -1;
    for (const c of track.clips) {
      if (!POSES.includes(c.pose)) throw new Error('未知动作');
      if (![c.start, c.end, c.speed].every(finite) || c.start < 0 || c.start < end || c.end <= c.start || c.end > t.duration || c.speed <= 0 || c.speed > 10) throw new Error('动作片段重叠或时间/速度无效');
      end = c.end;
    }
  }
}
export function upsertKey<T extends { time: number }>(keys: T[], key: T): T[] { return [...keys.filter(k => k.time !== key.time), key].sort((a, b) => a.time - b.time); }
export function sampleKeys<T extends { time: number }>(keys: T[], time: number): T | null {
  if (!keys.length) return null;
  if (time <= keys[0].time) return keys[0];
  const right = keys.findIndex(k => k.time > time);
  if (right < 0) return keys[keys.length - 1];
  const a = keys[right - 1], b = keys[right], f = (time - a.time) / (b.time - a.time);
  const out = { ...a, time } as T;
  for (const name of Object.keys(a)) {
    if (name === 'time') continue;
    const key = name as keyof T, av = a[key], bv = b[key];
    if (typeof av === 'number' && typeof bv === 'number') {
      const delta = name === 'azim' || name === 'yawDeg' ? ((bv - av + 540) % 360) - 180 : bv - av;
      out[key] = (av + delta * f) as T[keyof T];
    } else if (Array.isArray(av) && Array.isArray(bv)) out[key] = av.map((v: number, i) => v + (bv[i] - v) * f) as T[keyof T];
  }
  return out;
}
/** Arc length timing gives drawn paths constant spatial speed. */
export function pathKeys(points: Vec3[], start: number, end: number, base: ObjectKey): ObjectKey[] {
  const clean = points.filter((p, i) => !i || Math.hypot(...p.map((v, j) => v - points[i - 1][j])) > 0.001);
  if (clean.length < 2 || end <= start) throw new Error('请绘制至少两个不同路径点并保留运动时长');
  const distances = [0];
  for (let i = 1; i < clean.length; i++) distances.push(distances[i - 1] + Math.hypot(...clean[i].map((v, j) => v - clean[i - 1][j])));
  return clean.map((position, i) => ({ ...base, position, time: start + distances[i] / distances[distances.length - 1] * (end - start) }));
}
