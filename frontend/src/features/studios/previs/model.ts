import type { ThreeDSceneSnapshot } from '@/features/viewer-kit/three-d/engine/viewerApp';
export type Vec = [number, number, number];
export type Actor = { id: string; name: string; humanoid: boolean; position: Vec; yaw: number; characterRef?: string };
export type Clip = { id: string; actorId: string; action: 'walk' | 'run' | 'turn' | 'sit'; start: number; duration: number; target: Vec; yaw: number };
export type CameraKey = { id: string; time: number; position: Vec; target: Vec };
export type Scene = { actors: Actor[]; clips: Clip[]; camera: CameraKey[]; light: { yaw: number; intensity: number }; props: { id: string; position: Vec; scale: Vec }[]; reference?: string; source?: { type: string; id: string; revision?: string } };
export const emptyScene = (): Scene => ({ actors: [], clips: [], camera: [{ id: 'camera-start', time: 0, position: [8, 6, 10], target: [0, 1, 0] }], light: { yaw: 35, intensity: 1.4 }, props: [] });
export const durationOf = (scene: Scene) => Math.max(5, ...scene.clips.map(c => c.start + c.duration), ...scene.camera.map(c => c.time));
const mix = (a: number, b: number, t: number) => a + (b - a) * t;
export const mixVec = (a: Vec, b: Vec, t: number): Vec => a.map((v, i) => mix(v, b[i], t)) as Vec;
export function validateScene(scene: Scene): string | null {
  if (scene.actors.some(a => !a.name.trim() || ![...a.position, a.yaw].every(Number.isFinite))) return '演员名称或站位无效';
  if (![scene.light.yaw, scene.light.intensity].every(Number.isFinite) || scene.light.intensity < 0 || scene.light.intensity > 20) return '灯光亮度范围为 0–20';
  if (scene.props.some(p => ![...p.position, ...p.scale].every(Number.isFinite) || p.scale.some(n => n <= 0 || n > 1000))) return '物件尺寸必须在 0–1000 米之间';
  for (const a of scene.actors) {
    const clips = scene.clips.filter(c => c.actorId === a.id).sort((a, b) => a.start - b.start);
    if (!a.humanoid && clips.length) return `${a.name} 未确认人形适配，不能使用人形动作`;
    for (let i = 0; i < clips.length; i++) {
      const c = clips[i];
      if (![c.start, c.duration, c.yaw, ...c.target].every(Number.isFinite) || c.start < 0 || c.duration < 0.1) return '开始时间不能小于 0，时长至少 0.1 秒';
      if (c.start + c.duration > 3600) return '预演不能超过一小时';
      if (i && c.start < clips[i - 1].start + clips[i - 1].duration - 0.00001) return `${a.name} 的主动作重叠`;
    }
  }
  if (scene.clips.some(c => !scene.actors.some(a => a.id === c.actorId))) return '动作引用的演员不存在';
  if (!scene.camera.length || scene.camera.some(k => ![k.time, ...k.position, ...k.target].every(Number.isFinite) || k.time < 0)) return '摄影机关键帧无效';
  if (new Set(scene.camera.map(k => k.time)).size !== scene.camera.length) return '摄影机关键帧时间不能重复';
  return null;
}
export function sampleActor(actor: Actor, clips: Clip[], time: number) {
  let position = actor.position; let yaw = actor.yaw; let sit = 0; let stride = 0;
  for (const c of clips.filter(c => c.actorId === actor.id).sort((a, b) => a.start - b.start)) {
    if (time < c.start) break;
    const t = Math.min(1, (time - c.start) / c.duration);
    const smooth = t * t * (3 - 2 * t);
    const targetYaw = c.action === 'walk' || c.action === 'run' ? Math.atan2(c.target[0] - position[0], c.target[2] - position[2]) * 180 / Math.PI : c.yaw;
    if (c.action !== 'turn') position = mixVec(position, c.target, smooth);
    yaw += (((targetYaw - yaw + 540) % 360) - 180) * smooth;
    sit = mix(sit, c.action === 'sit' ? 1 : 0, smooth);
    stride = t < 1 && (c.action === 'walk' || c.action === 'run') ? Math.sin((time - c.start) * (c.action === 'run' ? 13 : 8)) * Math.sin(Math.PI * t) * (c.action === 'run' ? 55 : 30) : 0;
    if (t < 1) break;
  }
  return { position, yaw, sit, stride };
}
export function importDirectorScene(snapshot: ThreeDSceneSnapshot): Scene {
  if (snapshot.schemaVersion !== 1 || !Array.isArray(snapshot.actors) || !Array.isArray(snapshot.props)) throw new Error('需要 director_world / viewer-kit 的场景快照 schemaVersion 1');
  const scene = emptyScene();
  const positionOf = (item: ThreeDSceneSnapshot['actors'][number]): Vec => {
    if (item.placement?.space === 'pano_view') throw new Error('全景视角坐标暂不能映射为地面路径，请先转换为 world 坐标');
    const position = item.placement?.space === 'world' ? item.placement.position : item.position;
    if (!Array.isArray(position) || position.length !== 3 || !position.every(Number.isFinite)) throw new Error('场景物件坐标无效');
    return position;
  };
  scene.actors = snapshot.actors.map(item => { const ref = (item as typeof item & { characterRef?: unknown }).characterRef; return { id: crypto.randomUUID(), name: item.label, humanoid: false, position: positionOf(item), yaw: item.placement?.yawDeg ?? item.yawDeg, ...(typeof ref === 'string' && ref ? { characterRef: ref } : {}) }; });
  scene.props = [...snapshot.props, ...(snapshot.stagings ?? [])].map(item => ({ id: crypto.randomUUID(), position: positionOf(item), scale: item.scale }));
  if (snapshot.camera) {
    const c = snapshot.camera, az = c.azim * Math.PI / 180, el = c.elev * Math.PI / 180;
    scene.camera[0] = { id: 'imported-camera', time: 0, target: c.focalPoint, position: [c.focalPoint[0] + c.distance * Math.cos(el) * Math.sin(az), c.focalPoint[1] + c.distance * Math.sin(el), c.focalPoint[2] + c.distance * Math.cos(el) * Math.cos(az)] };
  }
  return scene;
}
export function sampleCamera(keys: CameraKey[], time: number) {
  const sorted = [...keys].sort((a, b) => a.time - b.time);
  const next = sorted.findIndex(k => k.time > time);
  if (next < 0) return sorted[sorted.length - 1];
  if (next === 0) return sorted[0];
  const a = sorted[next - 1], b = sorted[next], t = (time - a.time) / (b.time - a.time);
  return { ...a, position: mixVec(a.position, b.position, t), target: mixVec(a.target, b.target, t) };
}
export function parseActions(text: string, actor: Actor, start: number): Clip[] {
  if (!actor.humanoid) throw new Error('请先确认演员为人形');
  const clips: Clip[] = [];
  for (const line of text.split(/[；;\n]+/).filter(s => s.trim())) {
    const m = line.trim().match(/^(走|跑|转身|坐下)\s*(\d+(?:\.\d+)?)\s*秒\s*(?:到\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)|朝\s*(-?\d+(?:\.\d+)?)\s*度)$/);
    if (!m) throw new Error('仅支持：走/跑/坐下 3 秒 到 4,2；转身 2 秒 朝 90 度');
    const action = ({ 走: 'walk', 跑: 'run', 转身: 'turn', 坐下: 'sit' } as const)[m[1] as '走'];
    clips.push({ id: crypto.randomUUID(), actorId: actor.id, action, start, duration: Number(m[2]), target: m[3] ? [Number(m[3]), 0, Number(m[4])] : actor.position, yaw: Number(m[5] ?? actor.yaw) });
    start += Number(m[2]);
  }
  if (!clips.length) throw new Error('请输入动作指令');
  return clips;
}
export function parseSceneCommand(text: string, scene: Scene, actorId: string, time: number): Scene {
  const match = text.trim().match(/^(演员站位|添加座椅|机位|灯光)\s+(-?\d+(?:\.\d+)?)[,，]\s*(-?\d+(?:\.\d+)?)(?:[,，]\s*(-?\d+(?:\.\d+)?))?$/);
  if (!match) throw new Error('本地搭景仅支持：演员站位 2,3；添加座椅 2,3；机位 8,6,10；灯光 35,1.4（方向、亮度）');
  const [, kind, a, b, c] = match;
  if (kind === '演员站位') {
    if (!scene.actors.some(actor => actor.id === actorId)) throw new Error('请先选择演员');
    return { ...scene, actors: scene.actors.map(actor => actor.id === actorId ? { ...actor, position: [Number(a), 0, Number(b)] } : actor) };
  }
  if (kind === '添加座椅') return { ...scene, props: [...scene.props, { id: crypto.randomUUID(), position: [Number(a), 0.3, Number(b)], scale: [1, 0.6, 1] }] };
  if (kind === '灯光') { if (Number(b) < 0 || Number(b) > 20) throw new Error('灯光亮度范围为 0–20'); return { ...scene, light: { yaw: Number(a), intensity: Number(b) } }; }
  if (!c) throw new Error('机位需要 X,Y,Z 三个坐标');
  return { ...scene, camera: [...scene.camera.filter(key => key.time !== time), { id: crypto.randomUUID(), time, position: [Number(a), Number(b), Number(c)], target: scene.actors.find(actor => actor.id === actorId)?.position ?? [0, 1, 0] }] };
}
