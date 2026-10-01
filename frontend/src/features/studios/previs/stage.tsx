import { useEffect, useRef, useState } from 'react';
import * as pc from 'playcanvas';
import { sampleActor, sampleCamera, type Scene, type Vec } from './model';

export function PrevisStage({ scene, time, canvasRef, onError, onGround, onStroke }: { scene: Scene; time: number; canvasRef: React.RefObject<HTMLCanvasElement | null>; onError: (s: string) => void; onGround: (p: Vec, dragging?: boolean) => void; onStroke?: (points: Vec[]) => void }) {
  const live = useRef({ scene, time, onGround, onStroke });
  const [trace, setTrace] = useState<string[]>([]);
  useEffect(() => { setTrace([]); }, [scene.camera, onStroke === undefined]);
  live.current = { scene, time, onGround, onStroke };
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let app: pc.Application | undefined;
    let observer: ResizeObserver | undefined;
    let pointerStart: { x: number; y: number } | undefined;
    let stroke: Vec[] = [];
    const cleanups: (() => void)[] = [];
    try {
      app = new pc.Application(canvas, { graphicsDeviceOptions: { antialias: true, preserveDrawingBuffer: true, alpha: false } });
      app.setCanvasResolution(pc.RESOLUTION_AUTO);
      const world = new pc.Entity('预演场景'); app.root.addChild(world);
      const camera = new pc.Entity('摄影机');
      camera.addComponent('camera', { clearColor: new pc.Color(0.1, 0.12, 0.16), fov: 48, farClip: 200 });
      app.root.addChild(camera);
      const light = new pc.Entity('主光'); light.addComponent('light', { type: 'directional', intensity: 1.4, castShadows: true, shadowBias: 0.2, normalOffsetBias: 0.05, shadowDistance: 30, shadowResolution: 2048 }); app.root.addChild(light);
      app.scene.ambientLight = new pc.Color(0.45, 0.45, 0.48);
      const material = new pc.StandardMaterial(); material.diffuse = new pc.Color(0.86, 0.88, 0.9); material.update();
      const floorMaterial = new pc.StandardMaterial(); floorMaterial.diffuse = new pc.Color(0.27, 0.3, 0.34); floorMaterial.update();
      function part(parent: pc.Entity, name: string, type: string, position: Vec, scale: Vec, mat = material) {
        const e = new pc.Entity(name); e.addComponent('render', { type, material: mat }); e.setLocalPosition(...position); e.setLocalScale(...scale); parent.addChild(e); return e;
      }
      part(world, '地面', 'box', [0, -0.1, 0], [40, 0.2, 40], floorMaterial);
      const actorNodes = new Map<string, { root: pc.Entity; torso: pc.Entity; arms: pc.Entity[]; legs: pc.Entity[] }>();
      const propNodes = new Map<string, pc.Entity>();
      app.on('update', () => {
        const { scene: current, time: t } = live.current;
        const cam = sampleCamera(current.camera, t);
        camera.setPosition(...cam.position); camera.lookAt(new pc.Vec3(...cam.target));
        light.setEulerAngles(50, current.light.yaw, 0); if (light.light) light.light.intensity = current.light.intensity;
        for (const [id, node] of actorNodes) if (!current.actors.some(a => a.id === id)) { node.root.destroy(); actorNodes.delete(id); }
        for (const actor of current.actors) {
          let node = actorNodes.get(actor.id);
          if (!node) {
            const root = new pc.Entity(actor.name); world.addChild(root);
            const torso = new pc.Entity('身体'); root.addChild(torso);
            part(torso, '躯干', 'box', [0, 1.1, 0], [0.48, 0.65, 0.25]);
            part(torso, '头', 'sphere', [0, 1.65, 0], [0.32, 0.36, 0.32]);
            const arms = [-1, 1].map(side => { const joint = new pc.Entity('肩'); torso.addChild(joint); joint.setLocalPosition(side * 0.32, 1.4, 0); part(joint, '手臂', 'box', [0, -0.3, 0], [0.15, 0.62, 0.15]); return joint; });
            const legs = [-1, 1].map(side => { const joint = new pc.Entity('髋'); torso.addChild(joint); joint.setLocalPosition(side * 0.14, 0.8, 0); part(joint, '腿', 'box', [0, -0.39, 0], [0.18, 0.78, 0.2]); return joint; });
            node = { root, torso, arms, legs }; actorNodes.set(actor.id, node);
          }
          const pose = sampleActor(actor, current.clips, t);
          node.root.setPosition(...pose.position); node.root.setEulerAngles(0, pose.yaw, 0);
          node.torso.setLocalPosition(0, -pose.sit * 0.35, 0);
          node.arms.forEach((limb, i) => limb.setLocalEulerAngles(pose.stride * (i ? -1 : 1), 0, 0));
          node.legs.forEach((limb, i) => limb.setLocalEulerAngles(-pose.sit * 85 + pose.stride * (i ? 1 : -1), 0, 0));
        }
        for (const [id, node] of propNodes) if (!current.props.some(p => p.id === id)) { node.destroy(); propNodes.delete(id); }
        for (const prop of current.props) { let node = propNodes.get(prop.id); if (!node) { node = part(world, '物件', 'box', prop.position, prop.scale); propNodes.set(prop.id, node); } node.setPosition(...prop.position); node.setLocalScale(...prop.scale); }
      });
      const down = (event: PointerEvent) => { pointerStart = { x: event.clientX, y: event.clientY }; stroke = []; setTrace([]); canvas.setPointerCapture(event.pointerId); if (live.current.onStroke) pick(event, true); };
      const pick = (event: PointerEvent, dragging: boolean) => {
        if (!pointerStart || !camera.camera) return;
        const rect = canvas.getBoundingClientRect();
        const x = (event.clientX - rect.left) * canvas.width / rect.width, y = (event.clientY - rect.top) * canvas.height / rect.height;
        const from = camera.camera.screenToWorld(x, y, 0.1), to = camera.camera.screenToWorld(x, y, 100);
        const dy = to.y - from.y;
        if (Math.abs(dy) < 0.0001) return;
        const t = -from.y / dy;
        if (t > 0) {
          const position: Vec = [Number((from.x + (to.x - from.x) * t).toFixed(2)), 0, Number((from.z + (to.z - from.z) * t).toFixed(2))];
          if (live.current.onStroke) { if (stroke.length < 4000) { stroke.push(position); setTrace(prev => [...prev, `${(event.clientX-rect.left)/rect.width*100},${(event.clientY-rect.top)/rect.height*100}`]); } }
          else live.current.onGround(position, dragging);
        }
      };
      const move = (event: PointerEvent) => pick(event, true);
      const up = (event: PointerEvent) => { if (!pointerStart) return; pick(event, false); live.current.onStroke?.(stroke); stroke = []; pointerStart = undefined; };
      const cancel = () => { pointerStart = undefined; stroke = []; setTrace([]); };
      canvas.addEventListener('pointerdown', down); canvas.addEventListener('pointermove', move); canvas.addEventListener('pointerup', up); canvas.addEventListener('pointercancel', cancel);
      cleanups.push(() => { canvas.removeEventListener('pointerdown', down); canvas.removeEventListener('pointermove', move); canvas.removeEventListener('pointerup', up); canvas.removeEventListener('pointercancel', cancel); });
      const resize = () => { const width = canvas.parentElement?.clientWidth ?? 960; app?.resizeCanvas(width, Math.round(width * 9 / 16)); canvas.style.width = '100%'; canvas.style.height = 'auto'; };
      observer = new ResizeObserver(resize); observer.observe(canvas.parentElement!); resize();
      app.start();
      cleanups.push(() => { material.destroy(); floorMaterial.destroy(); });
    } catch (e) { onError(`三维初始化失败：${e instanceof Error ? e.message : String(e)}`); }
    return () => { observer?.disconnect(); app?.destroy(); cleanups.forEach(fn => fn()); };
  }, [canvasRef, onError]);
  return <div className="relative"><canvas aria-label="三维预演场景，拖动或点击设置地面目标" ref={canvasRef} className="aspect-video w-full touch-none rounded-lg bg-muted" />{onStroke && trace.length>1 && <svg aria-hidden="true" className="pointer-events-none absolute inset-0 h-full w-full text-primary" viewBox="0 0 100 100" preserveAspectRatio="none"><polyline points={trace.join(' ')} fill="none" stroke="currentColor" strokeWidth="3" vectorEffect="non-scaling-stroke" /></svg>}</div>;
}
