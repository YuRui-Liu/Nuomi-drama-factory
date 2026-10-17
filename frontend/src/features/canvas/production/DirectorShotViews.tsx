import { useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { resolveMediaUrl } from '@/lib/media-url';
import type { SemanticCandidate } from './directorWorkspaceApi';
import type { PathPoint, SceneBlocking } from './SceneBlockingEditor';

type RecordValue = Record<string, unknown>;
type Shot = RecordValue & { group: string; scene: string };
type TimedItem = { start: number; end: number; label: string; title: string };
export interface DirectorPreviewGroup {
  id: string;
  director_revision_id?: string;
  stages: {
    render?: { status?: string; grid_asset?: string; needs_regeneration?: boolean };
    video?: { status?: string; original_audio_path?: string; needs_regeneration?: boolean };
  };
}

function number(value: unknown, fallback = 0): number {
  const result = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(result) ? result : fallback;
}

function creativeOf(shot: Shot): RecordValue {
  return shot.creative && typeof shot.creative === 'object' ? shot.creative as RecordValue : {};
}

function performanceBeats(shot: Shot): Array<{ actor: string; at: number; behavior: string; event: string; end: number }> {
  const performance = creativeOf(shot).performance as RecordValue | undefined;
  const characters = Array.isArray(performance?.characters) ? performance.characters as RecordValue[] : [];
  const duration = Math.max(0, number(shot.duration_seconds));
  const beats = characters.flatMap(character => {
    const values = Array.isArray(character.beats) ? character.beats as RecordValue[] : [];
    const ordered = values.map((beat, index) => ({
      actor: String(character.subject_id ?? '人物'),
      at: Math.max(0, Math.min(duration, number(beat.at_seconds))),
      behavior: String(beat.behavior ?? ''),
      event: String(beat.event_anchor ?? ''),
      index,
    })).sort((left, right) => left.at - right.at || left.index - right.index);
    return ordered.map((beat, index) => ({ ...beat, end: Math.max(beat.at, ordered[index + 1]?.at ?? duration) }));
  });
  return beats.sort((left, right) => left.at - right.at || left.actor.localeCompare(right.actor));
}

function dialogueIntervals(shot: Shot, semantics?: SemanticCandidate): TimedItem[] {
  const sourceIds = Array.isArray(shot.dialogue_source_ids) ? shot.dialogue_source_ids.filter((id): id is string => typeof id === 'string') : [];
  if (!sourceIds.length) return [];
  const blocks = new Map<string, string>();
  for (const scene of semantics?.scenes ?? []) for (const block of scene.blocks) blocks.set(block.id, block.text);
  const duration = Math.max(0, number(shot.duration_seconds));
  const lines = sourceIds.map(id => ({ id, text: blocks.get(id) ?? '' }));
  const estimates = lines.map(line => {
    if (!line.text) return 0;
    const latinWords = line.text.trim().split(/\s+/).length;
    const cjkCharacters = [...line.text].filter(char => /[\u3400-\u9fff]/u.test(char)).length;
    return Math.max(0.4, cjkCharacters ? cjkCharacters / 4 : latinWords / 2.5);
  });
  const totalEstimate = estimates.reduce((sum, value) => sum + value, 0);
  const fit = totalEstimate > duration && totalEstimate > 0 ? duration / totalEstimate : 1;
  let cursor = 0;
  return lines.map((line, index) => {
    const estimate = estimates[index] * fit;
    const start = cursor;
    const end = Math.min(duration, start + estimate);
    cursor = end;
    const preview = line.text.trim() ? line.text.trim() : `对白来源 ${line.id}（文本未加载）`;
    return { start, end, label: `对白 ${index + 1}`, title: `${preview} · 时间按文本长度估算，非音频对齐` };
  });
}

function positionAt(path: PathPoint[] | undefined, seconds: number, fallback: { x: number; y: number }) {
  if (!path?.length || seconds < path[0].at_seconds) return fallback;
  const next = path.findIndex(point => point.at_seconds >= seconds);
  if (next < 0) return path[path.length - 1];
  if (next === 0) return path[0];
  const from = path[next - 1];
  const to = path[next];
  const span = Math.max(0.001, to.at_seconds - from.at_seconds);
  const ratio = Math.max(0, Math.min(1, (seconds - from.at_seconds) / span));
  return { x: from.x + (to.x - from.x) * ratio, y: from.y + (to.y - from.y) * ratio };
}

function SceneMapPreview({ sceneId, blocking, seconds }: { sceneId: string; blocking?: SceneBlocking; seconds: number }) {
  if (!sceneId || !blocking) return <p className="rounded border border-dashed p-3 text-sm text-muted-foreground">当前场景没有已保存的调度图；时间轴仍显示镜头动作与对白来源。</p>;
  const actors = blocking.actors.map(actor => ({ ...actor, ...positionAt(actor.path, seconds, actor) }));
  const props = blocking.props.map(prop => ({ ...prop, ...positionAt(prop.path, seconds, prop) }));
  const actorPositions = new Map(actors.map(actor => [actor.id, actor]));
  const propPositions = new Map(props.map(prop => [prop.id, prop]));
  return <figure className="space-y-1 rounded border p-3">
    <figcaption className="text-xs font-medium">{blocking.label} · 场景调度示意 · 场景内约 {seconds.toFixed(1)} 秒</figcaption>
    <svg viewBox="0 0 100 100" className="mx-auto aspect-square w-56 max-w-full rounded bg-muted" role="img" aria-label={`${blocking.label} 在当前场景时间的位置预览`}>
      <defs><marker id="animatic-blocking-arrow" markerWidth="4" markerHeight="4" refX="2.5" refY="2" orient="auto"><path d="M0,0 L4,2 L0,4 Z" fill="#64748b" /></marker></defs>
      <path d="M0 25H100 M0 50H100 M0 75H100 M25 0V100 M50 0V100 M75 0V100" stroke="currentColor" opacity="0.09" strokeWidth="0.35" />
      {actors.map(actor => {
        const target = actor.gaze_target ? actorPositions.get(actor.gaze_target) ?? propPositions.get(actor.gaze_target) : undefined;
        const path = [actor, ...(actor.path ?? [])];
        return <g key={actor.id}>
          {actor.path?.length ? <polyline points={path.map(point => `${point.x},${point.y}`).join(' ')} fill="none" stroke="#4685c6" strokeDasharray="1.5 1" strokeWidth="0.7" /> : null}
          {target && <line x1={actor.x} y1={actor.y} x2={target.x} y2={target.y} stroke="#64748b" strokeDasharray="1 1" markerEnd="url(#animatic-blocking-arrow)" />}
          <circle cx={actor.x} cy={actor.y} r="2.8" fill="#4685c6" /><text x={actor.x} y={actor.y - 4} textAnchor="middle" fontSize="3" fill="currentColor">{actor.label}</text>
        </g>;
      })}
      {props.map(prop => <g key={prop.id}>
        {prop.path?.length ? <polyline points={[prop, ...(prop.path ?? [])].map(point => `${point.x},${point.y}`).join(' ')} fill="none" stroke="#92400e" strokeDasharray="1.5 1" strokeWidth="0.7" /> : null}
        <rect x={prop.x - 2.2} y={prop.y - 2.2} width="4.4" height="4.4" rx="0.5" fill="#b7791f" /><text x={prop.x} y={prop.y - 4} textAnchor="middle" fontSize="3" fill="currentColor">{prop.label}</text>
      </g>)}
      {blocking.lights.map(light => <g key={light.id}><circle cx={light.x} cy={light.y} r="4.5" fill="#facc15" opacity={0.2 + light.intensity / 125} /><circle cx={light.x} cy={light.y} r="1.2" fill="#ca8a04" /><text x={light.x} y={light.y - 5} textAnchor="middle" fontSize="3" fill="currentColor">{light.label}</text></g>)}
      {blocking.camera && <g transform={`translate(${blocking.camera.x},${blocking.camera.y}) rotate(${blocking.camera.facing})`}><path d="M-3,-3 L5,0 L-3,3 Z" fill="#147d52" /><path d="M5,0 L25,-11 M5,0 L25,11" stroke="#147d52" strokeWidth="0.55" /></g>}
    </svg>
    <p className="text-xs text-muted-foreground">人物、道具路径与视线按已保存场景调度显示；此图是审阅示意，不是生成画面。</p>
  </figure>;
}

function IntervalTrack({ title, items, duration, localPosition, empty }: { title: string; items: TimedItem[]; duration: number; localPosition: number; empty: string }) {
  return <div className="grid grid-cols-[4.5rem_minmax(0,1fr)] items-start gap-2 text-xs">
    <span className="pt-1 text-muted-foreground">{title}</span>
    <div className="relative min-h-8 rounded bg-muted" aria-label={`${title}区间`}>
      {items.length ? items.map((item, index) => {
        const left = duration > 0 ? Math.max(0, Math.min(100, item.start / duration * 100)) : 0;
        const width = duration > 0 ? Math.max(2, Math.min(100 - left, (item.end - item.start) / duration * 100)) : 100;
        return <div key={`${item.label}:${index}`} title={`${item.title} · ${item.start.toFixed(1)}–${item.end.toFixed(1)} 秒`} className="absolute inset-y-0 flex items-center overflow-hidden rounded bg-primary/20 px-1 text-foreground" style={{ left: `${left}%`, width: `${width}%` }}>{item.label}</div>;
      }) : <span className="px-2 py-2 text-muted-foreground">{empty}</span>}
      {duration > 0 && <span className="absolute inset-y-0 w-0.5 bg-destructive" style={{ left: `${Math.max(0, Math.min(100, localPosition / duration * 100))}%` }} aria-hidden="true" />}
    </div>
  </div>;
}

export function DirectorShotViews({ groups, view, mediaGroups = [], semantics, sceneBlockings }: {
  groups: Array<Record<string, unknown>>;
  view: 'table' | 'animatic';
  mediaGroups?: DirectorPreviewGroup[];
  semantics?: SemanticCandidate;
  sceneBlockings?: Record<string, { current?: { blocking?: SceneBlocking } }>;
}) {
  const shots: Shot[] = groups.flatMap(group => {
    const context = group.creative_context as RecordValue | undefined;
    const groupId = String(group.id ?? group.group_id ?? '');
    const scene = String(context?.scene_id ?? group.scene_id ?? group.scene_anchor ?? '');
    return (Array.isArray(group.shots) ? group.shots as RecordValue[] : []).map(shot => ({ ...shot, group: groupId, scene }));
  });
  const [position, setPosition] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [mediaError, setMediaError] = useState('');
  const audio = useRef<HTMLAudioElement>(null);
  const playbackVersion = useRef(0);
  const durations = shots.map(shot => Math.max(0, number(shot.duration_seconds)));
  const total = durations.reduce((sum, duration) => sum + duration, 0);
  const shotStarts = durations.map((_, index) => durations.slice(0, index).reduce((sum, value) => sum + value, 0));
  useEffect(() => {
    if (!playing) return;
    let previous = performance.now();
    const timer = setInterval(() => {
      const now = performance.now(); const delta = (now - previous) / 1000; previous = now;
      setPosition(value => Math.min(total, value + delta));
    }, 100);
    return () => clearInterval(timer);
  }, [playing, total]);
  useEffect(() => { if (position >= total) setPlaying(false); }, [position, total]);
  let end = 0;
  const index = durations.findIndex(duration => { end += duration; return position < end; });
  const shotIndex = index < 0 ? shots.length - 1 : index;
  const shot = shots[shotIndex];
  const shotStart = shotStarts[shotIndex] ?? 0;
  const shotDuration = durations[shotIndex] ?? 0;
  const localPosition = Math.max(0, Math.min(shotDuration, position - shotStart));
  const currentMedia = mediaGroups.find(item => item.id === shot?.group);
  const render = currentMedia?.stages.render;
  const video = currentMedia?.stages.video;
  const boardUrl = !render?.needs_regeneration && render?.grid_asset ? resolveMediaUrl(render.grid_asset) : null;
  const audioUrl = !video?.needs_regeneration && video?.original_audio_path ? resolveMediaUrl(video.original_audio_path) : null;
  const groupStart = shot ? shotStarts[shots.findIndex(item => item.group === shot.group)] ?? 0 : 0;
  const sceneStartIndex = shot?.scene ? shots.findIndex(item => item.scene === shot.scene) : -1;
  const sceneStart = sceneStartIndex >= 0 ? shotStarts[sceneStartIndex] : shotStart;
  const sceneSeconds = Math.max(0, position - sceneStart);
  const blocking = shot?.scene ? sceneBlockings?.[shot.scene]?.current?.blocking : undefined;
  const beats = useMemo(() => shot ? performanceBeats(shot) : [], [shot]);
  const actionIntervals: TimedItem[] = beats.length
    ? beats.map(beat => ({ start: beat.at, end: beat.end, label: beat.actor, title: [beat.event, beat.behavior].filter(Boolean).join(' · ') }))
    : shot?.action ? [{ start: 0, end: shotDuration, label: '镜头动作', title: String(shot.action) }] : [];
  const dialogue = shot ? dialogueIntervals(shot, semantics) : [];
  const contentKey = JSON.stringify([shots.map(item => [item.group, item.scene, item.id, item.duration_seconds]), mediaGroups, semantics?.revision_id]);
  useEffect(() => { setPlaying(false); setPosition(0); setMediaError(''); }, [contentKey]);
  useEffect(() => {
    const player = audio.current;
    return () => { player?.pause(); };
  }, [audioUrl, view]);
  useEffect(() => { if (view !== 'animatic') setPlaying(false); }, [view]);
  useEffect(() => {
    playbackVersion.current += 1;
    return () => { playbackVersion.current += 1; };
  }, [playing, audioUrl, view]);
  useEffect(() => {
    const player = audio.current;
    if (!player || !audioUrl) return;
    if (!playing) { player.pause(); return; }
    const target = Math.max(0, position - groupStart);
    if (Math.abs(player.currentTime - target) > 0.3) player.currentTime = target;
    const version = playbackVersion.current;
    if (player.paused) void player.play().catch(() => {
      if (version === playbackVersion.current && audio.current === player) { setPlaying(false); setMediaError('已有音轨无法播放，请检查文件后重试。'); }
    });
  }, [playing, position, groupStart, audioUrl]);
  if (view === 'table') return <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr>{['组 / 镜头', '场景', '景别 / 摄影', '人物行动', '对白来源', '起止状态', '时长'].map(label => <th className="border-b p-2" key={label}>{label}</th>)}</tr></thead><tbody>
    {shots.map((item, i) => {
      const lineItems = dialogueIntervals(item, semantics);
      const performance = creativeOf(item).performance as RecordValue | undefined;
      const characters = Array.isArray(performance?.characters) ? performance.characters as RecordValue[] : [];
      return <tr key={`${item.group}:${String(item.id ?? i)}`}><td className="border-b p-2">{item.group} / {String(item.id ?? i + 1)}</td><td className="border-b p-2">{item.scene}</td><td className="border-b p-2">{String(item.shot_size ?? '')}<br />{String(item.camera_angle ?? '')}<br />{String(item.camera_motion ?? '')}</td><td className="border-b p-2">{String(item.subject ?? '')}<br />{String(item.action ?? '')}{characters.map(character => <div key={String(character.subject_id)}>{String(character.subject_id)} · {Array.isArray(character.beats) ? (character.beats as RecordValue[]).map(beat => `${number(beat.at_seconds).toFixed(1)}s ${String(beat.behavior ?? '')}`).join(' / ') : ''}</div>)}</td><td className="border-b p-2">{lineItems.map(line => <div key={line.title}>{line.title.split(' · 时间')[0]}</div>)}</td><td className="border-b p-2">{String(item.visible_start_state ?? '')} → {String(item.visible_end_state ?? '')}</td><td className="border-b p-2">{durations[i]} 秒</td></tr>;
    })}
  </tbody></table></div>;
  if (!shot) return <section aria-label="分镜时间预演" className="rounded-lg border p-4 text-sm text-muted-foreground">当前版本没有可预演镜头。</section>;
  return <section aria-label="分镜时间预演" className="space-y-3 rounded-lg border p-4">
    <div className="flex min-h-64 flex-col justify-center rounded bg-muted p-6 text-center">
      {boardUrl && <img className="max-h-80 object-contain" src={boardUrl} alt={`${shot.group} 整组分镜参考`} onError={() => { setPlaying(false); setMediaError('分镜图片无法读取，请检查文件后重试。'); }} />}
      <p className="text-xs text-muted-foreground">{boardUrl ? '整组分镜参考 · 按镜头目标时长预演' : '文字占位预演'} · {shot.group} / {String(shot.id ?? '')}</p>
      {render?.needs_regeneration && <p className="text-xs text-amber-600">分镜来源已更新，当前使用文字占位。</p>}
      <p className="mt-3 text-xl">{String(shot.subject ?? '')}</p><p className="mt-3">{String(shot.action ?? '暂无镜头')}</p><p className="mt-3 text-sm">{String(shot.shot_size ?? '')} · {String(shot.camera_motion ?? '')}</p>
    </div>
    {audioUrl ? <><audio key={audioUrl} ref={audio} src={audioUrl} preload="metadata" aria-label="当前组已有原音" onError={() => { setPlaying(false); setMediaError('已有音轨无法读取，请检查文件后重试。'); }} /><p className="text-xs text-muted-foreground">播放当前组已有原音；时间轴依据镜头目标时长。对白区间按来源文本估算，未做音频对齐。</p></> : <p className="text-xs text-muted-foreground">当前组无可用原音；使用文字、场景调度和目标时长提示。</p>}
    {mediaError && <p role="alert" className="text-sm text-destructive">{mediaError}</p>}
    <div className="space-y-2 rounded-lg border p-3">
      <h3 className="text-sm font-medium">镜头内动作与对白区间 · {String(shot.id ?? '')} · {shotDuration.toFixed(1)} 秒</h3>
      <IntervalTrack title="人物动作" items={actionIntervals} duration={shotDuration} localPosition={localPosition} empty="没有单独的动作节拍，以镜头动作描述为准。" />
      <IntervalTrack title="对白来源" items={dialogue} duration={shotDuration} localPosition={localPosition} empty="此镜头没有关联对白来源。" />
      <div className="flex justify-between text-[11px] text-muted-foreground"><span>0.0 秒</span><span>当前镜头时间 {localPosition.toFixed(1)} 秒</span><span>{shotDuration.toFixed(1)} 秒</span></div>
      {dialogue.length > 0 && <div className="space-y-1 text-xs">{dialogue.map((item, index) => <p key={`${item.label}:${index}`} title={item.title}><span className="font-medium">{item.label} · 约 {item.start.toFixed(1)}–{item.end.toFixed(1)} 秒：</span>{item.title.split(' · 时间')[0]}</p>)}</div>}
    </div>
    <SceneMapPreview sceneId={shot.scene} blocking={blocking} seconds={sceneSeconds} />
    <input aria-label="预演时间" className="w-full" type="range" min={0} max={total || 1} step={0.1} value={Math.min(position, total)} onChange={event => setPosition(Number(event.target.value))} />
    <div className="flex items-center gap-3"><Button disabled={!total} onClick={() => { setMediaError(''); if (position >= total) setPosition(0); setPlaying(value => !value); }}>{playing ? '暂停' : '播放预演'}</Button><span>{position.toFixed(1)} / {total.toFixed(1)} 秒</span><span className="text-xs text-muted-foreground">场景 {shot.scene || '未绑定'} · 场景时间 {sceneSeconds.toFixed(1)} 秒</span></div>
  </section>;
}
