import { useEffect, useState } from 'react';
import { loadAudioPeaks, PEAK_BUCKETS_PER_SEC } from '../compose/audioPeaks';
import type { MusicClip } from './types';
export function MusicWaveform({ url, clip }: {
    url?: string;
    clip: MusicClip;
}) {
    const [peaks, setPeaks] = useState<Float32Array | null>(null);
    useEffect(() => { let active = true; setPeaks(null); if (url)
        void loadAudioPeaks(url).then(p => { if (active)
            setPeaks(p); }).catch(() => { }); return () => { active = false; }; }, [url]);
    if (!peaks)
        return null;
    const points = Array.from({ length: 100 }, (_, i) => {
        let ms = clip.sourceInMs + clip.lengthMs * i / 100;
        if (clip.loop)
            ms = clip.loop.startMs + (ms - clip.loop.startMs) % (clip.loop.endMs - clip.loop.startMs);
        const peak = peaks[Math.floor(ms / 1000 * PEAK_BUCKETS_PER_SEC)] || 0;
        return `M${i},${15 - peak * 14}v${peak * 28}`;
    }).join(' ');
    return <svg aria-hidden="true" viewBox="0 0 100 30" preserveAspectRatio="none" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', opacity: .22, pointerEvents: 'none' }}><path d={points} stroke="currentColor" strokeWidth=".7"/></svg>;
}
