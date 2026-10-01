import type { CameraKey, Vec } from './model';

/** Distribute a ground-plane stroke over an explicit camera time range. */
export function cameraStroke(points: Vec[], start: number, duration: number, height: number, target: Vec): CameraKey[] {
  if (![start, duration, height, ...target, ...points.flat()].every(Number.isFinite) || start < 0 || duration < 0.1 || start + duration > 3600 || height <= 0) throw new Error('路径时间或高度无效，预演最长一小时');
  const sampled = points.filter((_, i) => i === 0 || i === points.length - 1 || i % Math.max(1, Math.ceil(points.length / 60)) === 0);
  const clean: Vec[] = [];
  for (const p of sampled) { const last = clean[clean.length - 1]; if (!last || Math.hypot(p[0] - last[0], p[2] - last[2]) > 0.02) clean.push(p); }
  if (clean.length < 2) throw new Error('请按住画面拖出一条路径，再松开鼠标');
  const distances = [0];
  for (let i = 1; i < clean.length; i++) distances.push(distances[i - 1] + Math.hypot(clean[i][0] - clean[i - 1][0], clean[i][2] - clean[i - 1][2]));
  const total = distances[distances.length - 1];
  return clean.map((p, i) => ({ id: crypto.randomUUID(), time: start + distances[i] / total * duration, position: [p[0], height, p[2]], target: [...target] }));
}
