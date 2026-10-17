import { useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { Button } from '@/components/ui/button';

export interface PathPoint { x: number; y: number; at_seconds: number }
interface Positioned { id: string; label: string; x: number; y: number; facing: number }
interface SceneActor extends Positioned { gaze_target?: string; path?: PathPoint[] }
interface SceneProp extends Positioned { held_by?: string; path?: PathPoint[] }
interface SceneLight extends Positioned { intensity: number }
interface SceneCamera { x: number; y: number; facing: number }
export interface SceneBlocking {
  label: string;
  actors: SceneActor[];
  props: SceneProp[];
  camera: SceneCamera | null;
  lights: SceneLight[];
}

type EntityKind = 'camera' | 'actor' | 'prop' | 'light';
type DragTarget = { pointerId: number; kind: Exclude<EntityKind, 'camera'> | 'camera'; id?: string; pathIndex?: number };
const origin = { x: 50, y: 50 };

function normalizeBlocking(value?: SceneBlocking): SceneBlocking {
  return {
    label: value?.label ?? '',
    actors: (value?.actors ?? []).map(item => ({ ...item, path: item.path ?? [], gaze_target: item.gaze_target ?? '' })),
    props: (value?.props ?? []).map(item => ({ ...item, path: item.path ?? [], held_by: item.held_by ?? '' })),
    camera: value?.camera ?? { x: 50, y: 85, facing: 270 },
    lights: (value?.lights ?? []).map(item => ({ ...item, facing: item.facing ?? 0 })),
  };
}

function mapPoint(event: ReactPointerEvent<SVGSVGElement>): PathPoint {
  const rect = event.currentTarget.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(100, (event.clientX - rect.left) / rect.width * 100)),
    y: Math.max(0, Math.min(100, (event.clientY - rect.top) / rect.height * 100)),
    at_seconds: 0,
  };
}

function pointAt(path: PathPoint[] | undefined, time: number, fallback: { x: number; y: number }) {
  if (!path?.length || time < path[0].at_seconds) return fallback;
  if (time === path[0].at_seconds) return path[0];
  const nextIndex = path.findIndex(point => point.at_seconds >= time);
  if (nextIndex < 0) return path[path.length - 1];
  const before = path[nextIndex - 1];
  const after = path[nextIndex];
  const span = Math.max(0.001, after.at_seconds - before.at_seconds);
  const ratio = Math.max(0, Math.min(1, (time - before.at_seconds) / span));
  return { x: before.x + (after.x - before.x) * ratio, y: before.y + (after.y - before.y) * ratio };
}

export function SceneBlockingEditor({ initial, busy, onSave, subjects = [], lightIds = [], propIds = [] }: {
  initial?: SceneBlocking;
  busy: boolean;
  onSave: (blocking: SceneBlocking) => void;
  subjects?: string[];
  lightIds?: string[];
  propIds?: string[];
}) {
  const [draft, setDraft] = useState(() => normalizeBlocking(initial));
  const [selected, setSelected] = useState('camera');
  const [pathMode, setPathMode] = useState(false);
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<DragTarget | null>(null);
  const [playhead, setPlayhead] = useState(0);
  const [previewDuration, setPreviewDuration] = useState(8);
  const camera = draft.camera;
  const separator = selected.indexOf(':');
  const kindText = separator < 0 ? selected : selected.slice(0, separator);
  const entityId = separator < 0 ? '' : selected.slice(separator + 1);
  const kind = kindText as EntityKind;
  const selectedActor = kind === 'actor' ? draft.actors.find(item => item.id === entityId) : undefined;
  const selectedProp = kind === 'prop' ? draft.props.find(item => item.id === entityId) : undefined;
  const selectedLight = kind === 'light' ? draft.lights.find(item => item.id === entityId) : undefined;
  const current = selectedActor ?? selectedProp ?? selectedLight ?? (kind === 'camera' ? camera : undefined);
  const selectedPath = selectedActor?.path ?? selectedProp?.path ?? [];
  const actors = draft.actors.map(actor => ({ ...actor, ...pointAt(actor.path, playhead, actor) }));
  const props = draft.props.map(prop => ({ ...prop, ...pointAt(prop.path, playhead, prop) }));
  const actorPositions = new Map(actors.map(actor => [actor.id, actor]));
  const propPositions = new Map(props.map(prop => [prop.id, prop]));

  function move(kindToMove: EntityKind, id: string | undefined, x: number, y: number, pathIndex?: number) {
    setDraft(value => {
      if (kindToMove === 'camera') return { ...value, camera: value.camera ? { ...value.camera, x, y } : { ...origin, x, y, facing: 270 } };
      if (kindToMove === 'actor') return { ...value, actors: value.actors.map(item => item.id !== id ? item : pathIndex === undefined
        ? { ...item, x, y }
        : { ...item, path: (item.path ?? []).map((point, index) => index === pathIndex ? { ...point, x, y } : point) }) };
      if (kindToMove === 'prop') return { ...value, props: value.props.map(item => item.id !== id ? item : pathIndex === undefined
        ? { ...item, x, y }
        : { ...item, path: (item.path ?? []).map((point, index) => index === pathIndex ? { ...point, x, y } : point) }) };
      return { ...value, lights: value.lights.map(item => item.id === id ? { ...item, x, y } : item) };
    });
  }

  function beginDrag(event: ReactPointerEvent<SVGElement>, target: DragTarget) {
    event.preventDefault();
    event.stopPropagation();
    if (target.kind !== 'camera' && target.id) setSelected(`${target.kind}:${target.id}`);
    drag.current = target;
    svg.current?.setPointerCapture(event.pointerId);
  }

  function appendPathPoint(point: PathPoint) {
    if (kind !== 'actor' && kind !== 'prop') return;
    const id = entityId;
    setDraft(value => {
      const list = kind === 'actor' ? value.actors : value.props;
      const selectedEntity = list.find(item => item.id === id);
      if (!selectedEntity) return value;
      const path = selectedEntity.path ?? [];
      const lastTime = path.length ? path[path.length - 1].at_seconds : 0;
      const next = { ...point, at_seconds: Math.max(lastTime + 1, Math.ceil(playhead * 10) / 10) };
      if (kind === 'actor') return { ...value, actors: value.actors.map(item => item.id === id ? { ...item, path: [...path, next] } : item) };
      return { ...value, props: value.props.map(item => item.id === id ? { ...item, path: [...path, next] } : item) };
    });
  }

  function addObject(type: 'actor' | 'prop' | 'light', id: string) {
    if (!id) return;
    if (type === 'actor') {
      const actor = { id, label: id, x: 50, y: 40, facing: 90, gaze_target: '', path: [] as PathPoint[] };
      setDraft(value => ({ ...value, actors: [...value.actors, actor] }));
      setSelected(`actor:${id}`);
    } else if (type === 'prop') {
      const prop = { id, label: id, x: 50, y: 60, facing: 0, held_by: '', path: [] as PathPoint[] };
      setDraft(value => ({ ...value, props: [...value.props, prop] }));
      setSelected(`prop:${id}`);
    } else {
      const light = { id, label: id, x: 25, y: 20, facing: 90, intensity: 50 };
      setDraft(value => ({ ...value, lights: [...value.lights, light] }));
      setSelected(`light:${id}`);
    }
  }

  function changeCurrent(key: string, value: number | string) {
    setDraft(previous => {
      if (kind === 'camera') return { ...previous, camera: previous.camera ? { ...previous.camera, [key]: value } : previous.camera };
      if (kind === 'actor') return { ...previous, actors: previous.actors.map(item => item.id === entityId ? { ...item, [key]: value } : item) };
      if (kind === 'prop') return { ...previous, props: previous.props.map(item => item.id === entityId ? { ...item, [key]: value } : item) };
      return { ...previous, lights: previous.lights.map(item => item.id === entityId ? { ...item, [key]: value } : item) };
    });
  }

  function changePathPoint(index: number, key: keyof PathPoint, value: number) {
    if (kind !== 'actor' && kind !== 'prop') return;
    setDraft(previous => {
      const items = kind === 'actor' ? previous.actors : previous.props;
      const item = items.find(entry => entry.id === entityId);
      if (!item) return previous;
      const path = (item.path ?? []).map((point, pointIndex) => pointIndex === index ? { ...point, [key]: value } : point)
        .sort((left, right) => left.at_seconds - right.at_seconds);
      if (kind === 'actor') return { ...previous, actors: previous.actors.map(entry => entry.id === entityId ? { ...entry, path } : entry) };
      return { ...previous, props: previous.props.map(entry => entry.id === entityId ? { ...entry, path } : entry) };
    });
  }

  function removePathPoint(index: number) {
    if (kind !== 'actor' && kind !== 'prop') return;
    setDraft(previous => {
      if (kind === 'actor') return { ...previous, actors: previous.actors.map(item => item.id === entityId ? { ...item, path: (item.path ?? []).filter((_, pointIndex) => pointIndex !== index) } : item) };
      return { ...previous, props: previous.props.map(item => item.id === entityId ? { ...item, path: (item.path ?? []).filter((_, pointIndex) => pointIndex !== index) } : item) };
    });
  }

  function removeSelected() {
    if (kind === 'actor') {
      setDraft(value => ({
        ...value,
        actors: value.actors.filter(item => item.id !== entityId).map(item => item.gaze_target === entityId ? { ...item, gaze_target: '' } : item),
        props: value.props.map(item => item.held_by === entityId ? { ...item, held_by: '' } : item),
      }));
    } else if (kind === 'prop') setDraft(value => ({ ...value, props: value.props.filter(item => item.id !== entityId), actors: value.actors.map(item => item.gaze_target === entityId ? { ...item, gaze_target: '' } : item) }));
    else if (kind === 'light') setDraft(value => ({ ...value, lights: value.lights.filter(item => item.id !== entityId) }));
    setSelected('camera');
  }

  const objectSelect = (value: string) => setSelected(value);
  const selectionOptions = [
    <option key="camera" value="camera">摄影机</option>,
    ...draft.actors.map(item => <option key={`actor:${item.id}`} value={`actor:${item.id}`}>人物 · {item.label}</option>),
    ...draft.props.map(item => <option key={`prop:${item.id}`} value={`prop:${item.id}`}>道具 · {item.label}</option>),
    ...draft.lights.map(item => <option key={`light:${item.id}`} value={`light:${item.id}`}>光源 · {item.label}</option>),
  ];

  return <section className="space-y-3 rounded-xl border p-4">
    <label className="grid gap-1 text-sm">调度名称<input className="rounded border bg-background p-2" value={draft.label} onChange={event => setDraft(value => ({ ...value, label: event.target.value }))} /></label>
    <p className="text-xs text-muted-foreground">平面调度与构图视野随对象位置、朝向和时间更新。坐标与路径是制作参考，不代表生成画面的精确物理控制。</p>
    <svg ref={svg} viewBox="0 0 100 100" className="aspect-square w-80 max-w-full touch-none rounded-lg border bg-muted" role="group" aria-label="场景人物、道具、摄影机与光源平面调度示意"
      onPointerDown={event => { if (pathMode && (kind === 'actor' || kind === 'prop')) { appendPathPoint(mapPoint(event)); event.preventDefault(); } }}
      onPointerMove={event => { const target = drag.current; if (!target || target.pointerId !== event.pointerId) return; const point = mapPoint(event); move(target.kind, target.id, point.x, point.y, target.pathIndex); }}
      onPointerUp={event => { if (drag.current?.pointerId === event.pointerId) drag.current = null; }}
      onPointerCancel={event => { if (drag.current?.pointerId === event.pointerId) drag.current = null; }}>
      <defs><marker id="blocking-arrow" markerWidth="4" markerHeight="4" refX="2.5" refY="2" orient="auto"><path d="M0,0 L4,2 L0,4 Z" fill="#64748b" /></marker></defs>
      <path d="M0 25H100 M0 50H100 M0 75H100 M25 0V100 M50 0V100 M75 0V100" stroke="currentColor" opacity="0.09" strokeWidth="0.35" />
      {draft.actors.map(actor => {
        const position = actorPositions.get(actor.id) ?? actor;
        const target = actor.gaze_target ? actorPositions.get(actor.gaze_target) ?? propPositions.get(actor.gaze_target) : undefined;
        const path = [actor, ...(actor.path ?? [])];
        return <g key={`actor:${actor.id}`} onPointerDown={event => beginDrag(event, { pointerId: event.pointerId, kind: 'actor', id: actor.id })} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') setSelected(`actor:${actor.id}`); }} tabIndex={0} role="button" aria-label={`人物 ${actor.label}，选择并拖动`}>
          {actor.path?.length ? <polyline points={path.map(point => `${point.x},${point.y}`).join(' ')} fill="none" stroke="#4685c6" strokeDasharray="1.5 1" strokeWidth="0.7" /> : null}
          {target && <line x1={position.x} y1={position.y} x2={target.x} y2={target.y} stroke="#64748b" strokeDasharray="1 1" markerEnd="url(#blocking-arrow)" />}
          <circle cx={position.x} cy={position.y} r={selected === `actor:${actor.id}` ? 4 : 3} fill={selected === `actor:${actor.id}` ? '#e88b39' : '#4685c6'} stroke="white" strokeWidth="0.5" />
          <line x1={position.x} y1={position.y} x2={position.x + Math.cos(actor.facing * Math.PI / 180) * 7} y2={position.y + Math.sin(actor.facing * Math.PI / 180) * 7} stroke="currentColor" />
          <text x={position.x} y={position.y - 5} textAnchor="middle" fontSize="3" fill="currentColor">{actor.label}</text>
          {actor.path?.map((point, index) => <circle key={index} cx={point.x} cy={point.y} r="1.7" fill="white" stroke="#2563eb" strokeWidth="0.7" onPointerDown={event => beginDrag(event, { pointerId: event.pointerId, kind: 'actor', id: actor.id, pathIndex: index })} />)}
        </g>;
      })}
      {draft.props.map(prop => {
        const position = propPositions.get(prop.id) ?? prop;
        const path = [prop, ...(prop.path ?? [])];
        return <g key={`prop:${prop.id}`} onPointerDown={event => beginDrag(event, { pointerId: event.pointerId, kind: 'prop', id: prop.id })} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') setSelected(`prop:${prop.id}`); }} tabIndex={0} role="button" aria-label={`道具 ${prop.label}，选择并拖动`}>
          {prop.path?.length ? <polyline points={path.map(point => `${point.x},${point.y}`).join(' ')} fill="none" stroke="#92400e" strokeDasharray="1.5 1" strokeWidth="0.7" /> : null}
          <rect x={position.x - 2.5} y={position.y - 2.5} width="5" height="5" rx="0.7" fill={selected === `prop:${prop.id}` ? '#e88b39' : '#b7791f'} stroke="white" strokeWidth="0.5" />
          <text x={position.x} y={position.y - 4} textAnchor="middle" fontSize="3" fill="currentColor">{prop.label}</text>
          {prop.path?.map((point, index) => <circle key={index} cx={point.x} cy={point.y} r="1.7" fill="white" stroke="#92400e" strokeWidth="0.7" onPointerDown={event => beginDrag(event, { pointerId: event.pointerId, kind: 'prop', id: prop.id, pathIndex: index })} />)}
        </g>;
      })}
      {draft.lights.map(light => <g key={`light:${light.id}`} onPointerDown={event => beginDrag(event, { pointerId: event.pointerId, kind: 'light', id: light.id })} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') setSelected(`light:${light.id}`); }} tabIndex={0} role="button" aria-label={`光源 ${light.label}，选择并拖动`}>
        <circle cx={light.x} cy={light.y} r="5" fill="#facc15" opacity={0.18 + light.intensity / 125} /><circle cx={light.x} cy={light.y} r="1.4" fill="#ca8a04" />
        <line x1={light.x} y1={light.y} x2={light.x + Math.cos(light.facing * Math.PI / 180) * 7} y2={light.y + Math.sin(light.facing * Math.PI / 180) * 7} stroke="#a16207" />
        <text x={light.x} y={light.y - 6} textAnchor="middle" fontSize="3" fill="currentColor">{light.label}</text>
      </g>)}
      {camera && <g onPointerDown={event => beginDrag(event, { pointerId: event.pointerId, kind: 'camera' })} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') setSelected('camera'); }} tabIndex={0} role="button" aria-label="摄影机，选择并拖动">
        {(() => { const angle = camera.facing * Math.PI / 180; const left = angle - Math.PI / 7; const right = angle + Math.PI / 7; const edge = (r: number, a: number) => `${camera.x + Math.cos(a) * r},${camera.y + Math.sin(a) * r}`; return <path d={`M${camera.x},${camera.y} L${edge(30, left)} A30,30 0 0,1 ${edge(30, right)} Z`} fill="#59a780" opacity="0.12" stroke="#59a780" strokeWidth="0.5" />; })()}
        <g transform={`translate(${camera.x},${camera.y}) rotate(${camera.facing})`}><path d="M-3,-3 L5,0 L-3,3 Z" fill="#147d52" /><path d="M5,0 L10,-2 M5,0 L10,2" stroke="#147d52" strokeWidth="0.7" /></g>
        <text x={camera.x} y={camera.y + 6} textAnchor="middle" fontSize="3" fill="currentColor">机位 · 构图视野</text>
      </g>}
      {pathMode && <text x="2" y="5" fontSize="3.4" fill="#1d4ed8">路径编辑：点击画面添加路径点</text>}
    </svg>

    <div className="flex flex-wrap items-end gap-2">
      <label className="grid gap-1 text-sm">选中对象<select className="rounded border bg-background p-2" value={selected} onChange={event => objectSelect(event.target.value)}>{selectionOptions}</select></label>
      <label className="grid gap-1 text-sm">添加方案人物<select aria-label="添加方案人物" className="rounded border bg-background p-2" value="" onChange={event => addObject('actor', event.target.value)}><option value="">选择人物</option>{subjects.filter(id => !draft.actors.some(item => item.id === id)).map(id => <option key={id} value={id}>{id}</option>)}</select></label>
      <label className="grid gap-1 text-sm">添加方案道具<select aria-label="添加方案道具" className="rounded border bg-background p-2" value="" onChange={event => addObject('prop', event.target.value)}><option value="">选择道具</option>{propIds.filter(id => !draft.props.some(item => item.id === id)).map(id => <option key={id} value={id}>{id}</option>)}</select></label>
      <label className="grid gap-1 text-sm">添加方案光源<select aria-label="添加方案光源" className="rounded border bg-background p-2" value="" onChange={event => addObject('light', event.target.value)}><option value="">选择光源</option>{lightIds.filter(id => !draft.lights.some(item => item.id === id)).map(id => <option key={id} value={id}>{id}</option>)}</select></label>
    </div>
    {current && <div className="flex flex-wrap items-end gap-2">
      {(['x', 'y', 'facing'] as const).map(key => <label className="grid gap-1 text-sm" key={key}>{key === 'facing' ? '朝向（度）' : key.toUpperCase()}<input type="number" min={0} max={key === 'facing' ? 359 : 100} step={1} className="w-24 rounded border bg-background p-2" value={Number(current[key])} onChange={event => { const value = Number(event.target.value); if (Number.isFinite(value) && value >= 0 && value <= (key === 'facing' ? 359 : 100)) changeCurrent(key, value); }} /></label>)}
      {selectedLight && <label className="grid gap-1 text-sm">相对强度<input type="number" min={0} max={100} className="w-24 rounded border bg-background p-2" value={selectedLight.intensity} onChange={event => { const value = Number(event.target.value); if (Number.isFinite(value) && value >= 0 && value <= 100) changeCurrent('intensity', value); }} /></label>}
      {selectedActor && <>
        <label className="grid gap-1 text-sm">人物名称<input className="rounded border bg-background p-2" value={selectedActor.label} onChange={event => changeCurrent('label', event.target.value)} /></label>
        <label className="grid gap-1 text-sm">视线目标<select className="rounded border bg-background p-2" value={selectedActor.gaze_target ?? ''} onChange={event => changeCurrent('gaze_target', event.target.value)}><option value="">无明确目标</option>{[...draft.actors.filter(item => item.id !== entityId).map(item => ({ id: item.id, label: `人物 · ${item.label}` })), ...draft.props.map(item => ({ id: item.id, label: `道具 · ${item.label}` }))].map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
      </>}
      {selectedProp && <>
        <label className="grid gap-1 text-sm">道具名称<input className="rounded border bg-background p-2" value={selectedProp.label} onChange={event => changeCurrent('label', event.target.value)} /></label>
        <label className="grid gap-1 text-sm">持有角色<select className="rounded border bg-background p-2" value={selectedProp.held_by ?? ''} onChange={event => changeCurrent('held_by', event.target.value)}><option value="">未持有</option>{draft.actors.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
      </>}
      {(selectedActor || selectedProp) && <>
        <Button type="button" variant={pathMode ? 'default' : 'outline'} onClick={() => setPathMode(value => !value)}>编辑行动路径</Button>
        <Button type="button" variant="outline" onClick={() => appendPathPoint({ ...(selectedActor ?? selectedProp ?? origin), at_seconds: 0 })}>增加路径点</Button>
      </>}
      {(selectedActor || selectedProp || selectedLight) && <Button type="button" variant="outline" onClick={removeSelected}>从场景调度移除</Button>}
    </div>}

    {(selectedActor || selectedProp) && selectedPath.length > 0 && <div className="space-y-2 rounded border p-3">
      <h4 className="text-sm font-medium">路径关键点 · 当前预览 {playhead.toFixed(1)} 秒</h4>
      {selectedPath.map((point, index) => <div className="flex flex-wrap items-end gap-2" key={index}>
        <span className="pb-2 text-xs">点 {index + 1}</span>
        {(['x', 'y', 'at_seconds'] as const).map(key => <label className="grid gap-1 text-xs" key={key}>{key === 'at_seconds' ? '时间（秒）' : key.toUpperCase()}<input type="number" min={0} max={key === 'at_seconds' ? 3600 : 100} step={0.1} className="w-24 rounded border bg-background p-2" value={point[key]} onChange={event => { const value = Number(event.target.value); if (Number.isFinite(value) && value >= 0 && value <= (key === 'at_seconds' ? 3600 : 100)) changePathPoint(index, key, value); }} /></label>)}
        <Button type="button" variant="outline" onClick={() => removePathPoint(index)}>移除</Button>
      </div>)}
    </div>}

    <div className="space-y-2 rounded border border-dashed p-3">
      <h4 className="text-sm font-medium">构图示意联动</h4>
      <p className="text-xs text-muted-foreground">机位的扇形显示相机朝向与观察范围；人物视线线段、路径和道具持有关系按场景图同步预览。示意只供导演审阅。</p>
      <label className="grid gap-1 text-xs">路径预演位置 {playhead.toFixed(1)} 秒<input type="range" min={0} max={previewDuration || 1} step={0.1} value={playhead} onChange={event => setPlayhead(Number(event.target.value))} /></label>
      <label className="grid gap-1 text-xs">预演时长<input type="number" min={1} max={3600} className="w-24 rounded border bg-background p-2" value={previewDuration} onChange={event => { const value = Number(event.target.value); if (Number.isFinite(value) && value >= 1 && value <= 3600) { setPreviewDuration(value); setPlayhead(previous => Math.min(previous, value)); } }} /></label>
    </div>
    <Button disabled={busy || !draft.label.trim()} onClick={() => onSave(draft)}>保存调度新版本</Button>
  </section>;
}
