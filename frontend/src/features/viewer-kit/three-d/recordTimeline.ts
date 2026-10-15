import type { ViewerApp } from './engine/viewerApp';
export function supportedRecordingMime(): string | null {
  if (typeof MediaRecorder === 'undefined') return null;
  return ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/mp4'].find(m => MediaRecorder.isTypeSupported(m)) ?? null;
}
export async function recordTimeline(viewer: ViewerApp, signal: AbortSignal, progress: (value: number) => void, rotationOnly: boolean): Promise<Blob> {
  const mimeType = supportedRecordingMime();
  const canvas = viewer.app.graphicsDevice.canvas as HTMLCanvasElement;
  if (!mimeType || !canvas.captureStream) {
    const { recordFrames } = await import('./recordFrames');
    return recordFrames(viewer, signal, progress, rotationOnly);
  }
  const stream = canvas.captureStream(30);
  const chunks: Blob[] = [];
  let frame = 0;
  let recorder: MediaRecorder | undefined;
  let abortHandler: (() => void) | undefined;
  let visibilityHandler: (() => void) | undefined;
  try {
    const result = await new Promise<Blob>((resolve, reject) => {
      recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 6_000_000 });
      const abort = () => { if (recorder?.state !== 'inactive') recorder?.stop(); reject(new DOMException('已取消', 'AbortError')); };
      abortHandler = abort;
      signal.addEventListener('abort', abort, { once: true });
      visibilityHandler = () => { if (document.hidden) { reject(new Error('窗口已隐藏，导出已停止；请保持窗口可见后重试')); if (recorder?.state !== 'inactive') recorder?.stop(); } };
      document.addEventListener('visibilitychange', visibilityHandler);
      recorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
      recorder.onerror = () => { signal.removeEventListener('abort', abort); reject(new Error('视频录制失败')); };
      recorder.onstop = () => {
        signal.removeEventListener('abort', abort);
        if (signal.aborted) return reject(new DOMException('已取消', 'AbortError'));
        const blob = new Blob(chunks, { type: mimeType });
        if (!blob.size) reject(new Error('视频编码返回空文件')); else resolve(blob);
      };
      if (signal.aborted) { abort(); return; }
      viewer.seekTimeline(0, rotationOnly);
      recorder.start();
      const start = performance.now(), duration = viewer.getTimeline().duration;
      const tick = (now: number) => {
        if (signal.aborted) return;
        const time = Math.min(duration, (now - start) / 1000);
        try { viewer.seekTimeline(time, rotationOnly); } catch (error) { reject(error); return; }
        progress(time / duration);
        if (time >= duration) recorder?.stop(); else frame = requestAnimationFrame(tick);
      };
      frame = requestAnimationFrame(tick);
    });
    signal.throwIfAborted();
    const measured = await videoDuration(result);
    if (!Number.isFinite(measured) || Math.abs(measured - viewer.getTimeline().duration) > 1 / 30) {
      const { recordFrames } = await import('./recordFrames');
      return await recordFrames(viewer, signal, progress, rotationOnly);
    }
    return result;
  } finally {
    if (visibilityHandler) document.removeEventListener('visibilitychange', visibilityHandler);
    if (abortHandler) signal.removeEventListener('abort', abortHandler);
    cancelAnimationFrame(frame);
    if (recorder && recorder.state !== 'inactive') recorder.stop();
    stream.getTracks().forEach(track => track.stop());
  }
}

async function videoDuration(blob: Blob): Promise<number> {
  const url = URL.createObjectURL(blob), video = document.createElement('video');
  try {
    return await new Promise<number>(resolve => {
      const timer = setTimeout(() => resolve(NaN), 5000);
      const done = (duration: number) => { clearTimeout(timer); resolve(duration); };
      video.onloadedmetadata = () => done(video.duration);
      video.onerror = () => done(NaN);
      video.src = url;
    });
  } finally { video.removeAttribute('src'); video.load(); URL.revokeObjectURL(url); }
}
