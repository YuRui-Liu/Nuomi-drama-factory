import type { MusicClip, MusicPlan, MusicSource, MusicTrack } from './types';
export const id = () => crypto.randomUUID().replace(/-/g, '');
export const newTrack = (name = '配乐轨'): MusicTrack => ({ id: id(), name, gainDb: 0, muted: false, solo: false, clips: [] });
export function emptyPlan(source: MusicSource): MusicPlan { return { schemaVersion: 1, revision: 0, source, original: { muted: false, gainDb: 0 }, ducking: { enabled: false, gainDb: -12, attackMs: 150, releaseMs: 300, intervals: [] }, tracks: [newTrack('配乐 1')] }; }
function validate(p: MusicPlan) {
    for (const t of p.tracks) {
        let end = 0;
        for (const c of [...t.clips].sort((a, b) => a.startMs - b.startMs)) {
            if (![c.startMs, c.sourceInMs, c.lengthMs, c.fadeInMs, c.fadeOutMs].every(Number.isSafeInteger) || !Number.isFinite(c.gainDb))
                throw Error('片段时间或音量无效');
            if (c.loop && (!Number.isSafeInteger(c.loop.startMs) || !Number.isSafeInteger(c.loop.endMs) || c.loop.startMs < 0 || c.loop.endMs <= c.loop.startMs || c.sourceInMs < c.loop.startMs || c.sourceInMs >= c.loop.endMs))
                throw Error('循环区间无效，源入点须位于循环区间内');
            if (c.startMs < end || c.startMs < 0 || c.sourceInMs < 0 || c.lengthMs <= 0 || c.startMs + c.lengthMs > p.source.durationMs || c.fadeInMs < 0 || c.fadeOutMs < 0 || c.fadeInMs + c.fadeOutMs > c.lengthMs)
                throw Error('片段超出范围、淡变过长或同轨重叠');
            end = c.startMs + c.lengthMs;
        }
    }
    return p;
}
function sourceAt(c: MusicClip, delta: number) { if (!c.loop)
    return c.sourceInMs + delta; return c.loop.startMs + (c.sourceInMs - c.loop.startMs + delta) % (c.loop.endMs - c.loop.startMs); }
function portion(c: MusicClip, a: number, b: number): MusicClip { const length = b - a; const fadeIn = a === c.startMs ? Math.min(c.fadeInMs, length) : 0; return { ...c, id: id(), startMs: a, sourceInMs: sourceAt(c, a - c.startMs), lengthMs: length, fadeInMs: fadeIn, fadeOutMs: b === c.startMs + c.lengthMs ? Math.min(c.fadeOutMs, length - fadeIn) : 0 }; }
export function insertClip(plan: MusicPlan, trackId: string, clip: MusicClip, mode: 'ask' | 'replace' | 'layer'): MusicPlan {
    const p = structuredClone(plan);
    let t = p.tracks.find(t => t.id === trackId);
    if (!t)
        throw Error('请先选择配乐轨');
    if (mode === 'layer') {
        t = newTrack('叠加配乐');
        p.tracks.push(t);
    }
    const a = clip.startMs, b = a + clip.lengthMs;
    if (mode === 'replace')
        t.clips = t.clips.flatMap(c => { const end = c.startMs + c.lengthMs; if (end <= a || c.startMs >= b)
            return [c]; return [...(c.startMs < a ? [portion(c, c.startMs, a)] : []), ...(end > b ? [portion(c, b, end)] : [])]; });
    t.clips.push(clip);
    t.clips.sort((a, b) => a.startMs - b.startMs);
    return validate(p);
}
export function changeClip(plan: MusicPlan, clipId: string, changes: Partial<MusicClip>) { return validate({ ...plan, tracks: plan.tracks.map(t => ({ ...t, clips: t.clips.map(c => c.id === clipId ? { ...c, ...changes } : c) })) }); }
export function splitClip(plan: MusicPlan, clipId: string, timeMs: number) { return validate({ ...plan, tracks: plan.tracks.map(t => ({ ...t, clips: t.clips.flatMap(c => { if (c.id !== clipId)
            return [c]; if (timeMs <= c.startMs || timeMs >= c.startMs + c.lengthMs)
            throw Error('分割点必须位于片段内部'); return [portion(c, c.startMs, timeMs), portion(c, timeMs, c.startMs + c.lengthMs)]; }) })) }); }
export function duplicateClip(plan: MusicPlan, clipId: string) { for (const t of plan.tracks) {
    const c = t.clips.find(c => c.id === clipId);
    if (c)
        return insertClip(plan, t.id, { ...c, id: id() }, 'layer');
} return plan; }
export function removeClip(plan: MusicPlan, clipId: string) { return { ...plan, tracks: plan.tracks.map(t => ({ ...t, clips: t.clips.filter(c => c.id !== clipId) })) }; }
export interface History {
    past: MusicPlan[];
    present: MusicPlan;
    future: MusicPlan[];
}
export const remember = (h: History, p: MusicPlan): History => ({ past: [...h.past, h.present].slice(-100), present: p, future: [] });
export function undo(h: History): History { const p = h.past[h.past.length - 1]; return p ? { past: h.past.slice(0, -1), present: { ...p, revision: h.present.revision }, future: [h.present, ...h.future] } : h; }
export function redo(h: History): History { const p = h.future[0]; return p ? { past: [...h.past, h.present], present: { ...p, revision: h.present.revision }, future: h.future.slice(1) } : h; }
export const formatTime = (ms: number) => `${String(Math.floor(ms / 60000)).padStart(2, '0')}:${(ms % 60000 / 1000).toFixed(1).padStart(4, '0')}`;
