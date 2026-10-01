import { useEffect, useRef } from 'react';
export function MusicPreview({ videoUrl, audioUrl, stale, onTime }: {
    videoUrl: string;
    audioUrl?: string;
    stale: boolean;
    onTime: (ms: number) => void;
}) {
    const audio = useRef<HTMLAudioElement>(null), video = useRef<HTMLVideoElement>(null);
    useEffect(() => { video.current?.pause(); audio.current?.pause(); }, [audioUrl, stale]);
    const sync = () => { const v = video.current, a = audio.current; if (!v || !a || stale || !audioUrl)
        return; if (Math.abs(a.currentTime - v.currentTime) > .1)
        a.currentTime = v.currentTime; a.playbackRate = v.playbackRate; };
    return <section className="md-preview"><video ref={video} src={videoUrl} controls playsInline muted={Boolean(audioUrl) && !stale} onTimeUpdate={() => { sync(); onTime((video.current?.currentTime || 0) * 1000); }} onSeeking={sync} onRateChange={sync} onPlay={() => { if (audioUrl && !stale) {
        sync();
        void audio.current?.play().catch(() => video.current?.pause());
    } }} onPause={() => audio.current?.pause()} onEnded={() => audio.current?.pause()}/>{audioUrl && <audio ref={audio} src={audioUrl} preload="auto"/>}<small>{stale ? '配乐已修改，当前播放原片；点击「更新混音试听」听新安排' : audioUrl ? '正在播放配乐混音 · 与导出使用相同混音参数' : '当前播放原片 · 编排后可更新混音试听'}</small></section>;
}
