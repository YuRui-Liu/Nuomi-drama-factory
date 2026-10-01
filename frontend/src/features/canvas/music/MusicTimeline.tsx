import { MusicWaveform } from './MusicWaveform';
import { useRef } from 'react';
import type { MusicClip, MusicPlan, MusicTrack } from './types';
import { formatTime } from './timeline';
interface Props {
    urls?: Record<string, string>;
    plan: MusicPlan;
    trackId: string;
    clipId: string;
    range: [
        number,
        number
    ];
    onRange: (r: [
        number,
        number
    ]) => void;
    onTrack: (id: string) => void;
    onClip: (id: string) => void;
    onChangeTrack: (id: string, p: Partial<MusicTrack>) => void;
    onRemoveTrack: (id: string) => void;
    onMove: (id: string, ms: number) => void;
    names: Record<string, string>;
}
export function MusicTimeline({ plan, trackId, clipId, range, onRange, onTrack, onClip, onChangeTrack, onRemoveTrack, onMove, names, urls = {} }: Props) {
    const drag = useRef<{
        x: number;
        clip: MusicClip;
        width: number;
    } | null>(null);
    const selection = useRef<number | null>(null);
    const duration = plan.source.durationMs;
    return <section className="md-timeline" aria-label="多轨配乐时间线"><div className="md-ruler"><span>时间 / 秒</span>{Array.from({ length: 7 }, (_, i) => <small key={i}>{formatTime(duration * i / 6)}</small>)}</div>
 <div className="md-track"><div className="md-track-head">成片视频 <small>画面固定</small></div><div className="md-lane md-film" onPointerDown={e => { const r = e.currentTarget.getBoundingClientRect(); selection.current = Math.round((e.clientX - r.left) / r.width * duration); e.currentTarget.setPointerCapture(e.pointerId); }} onPointerMove={e => { if (selection.current === null)
        return; const r = e.currentTarget.getBoundingClientRect(); const end = Math.max(0, Math.min(duration, Math.round((e.clientX - r.left) / r.width * duration))); onRange([Math.min(selection.current, end), Math.max(selection.current, end)]); }} onPointerUp={() => { selection.current = null; }} onPointerCancel={() => { selection.current = null; }}><span>拖动框选任意剧情段落</span><div className="md-region" style={{ left: range[0] / duration * 100 + '%', width: (range[1] - range[0]) / duration * 100 + '%' }}/></div></div>
 <div className="md-track"><div className="md-track-head">原声 <small>独立保留</small></div><div className="md-lane md-original">{plan.original.muted ? '原声已静音' : `视频原声 · ${plan.original.gainDb} dB`}</div></div>
 {plan.tracks.map((track, i) => <div key={track.id} className={'md-track ' + (trackId === track.id ? 'is-active' : '')}>
  <div className="md-track-head" onClick={() => onTrack(track.id)}><input aria-label="轨道名称" value={track.name} onChange={e => onChangeTrack(track.id, { name: e.target.value })}/><div className="md-row"><button aria-label={`静音 ${track.name}`} aria-pressed={track.muted} className={track.muted ? 'active' : ''} onClick={() => onChangeTrack(track.id, { muted: !track.muted })}>M</button><button aria-label={`独奏 ${track.name}`} aria-pressed={track.solo} className={track.solo ? 'active' : ''} onClick={() => onChangeTrack(track.id, { solo: !track.solo })}>S</button><input aria-label={`${track.name} 音量`} type="range" min="-60" max="6" value={track.gainDb} onChange={e => onChangeTrack(track.id, { gainDb: Number(e.target.value) })}/><button aria-label={`删除 ${track.name}`} onClick={() => onRemoveTrack(track.id)}>×</button></div></div>
  <div className="md-lane" onClick={() => onTrack(track.id)}>{track.clips.map(clip => <button key={clip.id} className={'md-clip ' + (clipId === clip.id ? 'selected' : '')} style={{ left: clip.startMs / duration * 100 + '%', width: clip.lengthMs / duration * 100 + '%', background: i % 2 ? '#455377' : '#526a3e' }} title={`${names[clip.assetVersionId] || '配乐'} ${formatTime(clip.startMs)}–${formatTime(clip.startMs + clip.lengthMs)}`} onClick={e => { e.stopPropagation(); onTrack(track.id); onClip(clip.id); }} onPointerDown={e => { e.stopPropagation(); drag.current = { x: e.clientX, clip, width: e.currentTarget.parentElement!.getBoundingClientRect().width }; e.currentTarget.setPointerCapture(e.pointerId); }} onPointerUp={e => { if (!drag.current)
            return; const d = drag.current; drag.current = null; const delta = Math.round((e.clientX - d.x) / d.width * duration); if (Math.abs(delta) > 20)
            onMove(clip.id, Math.max(0, Math.min(duration - clip.lengthMs, clip.startMs + delta))); }} onPointerCancel={() => { drag.current = null; }}><MusicWaveform url={urls[clip.assetVersionId]} clip={clip}/>{names[clip.assetVersionId] || '配乐片段'}<small>{formatTime(clip.lengthMs)}</small></button>)}</div>
 </div>)}
 </section>;
}
