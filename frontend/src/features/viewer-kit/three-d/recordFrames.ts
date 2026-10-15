import { FFmpeg } from '@ffmpeg/ffmpeg';
import coreURL from '@ffmpeg/core?url';
import wasmURL from '@ffmpeg/core/wasm?url';
import classWorkerURL from '@ffmpeg/ffmpeg/worker?worker&url';
import type { ViewerApp } from './engine/viewerApp';
/** Offline frame sampling fallback: isolated encoder so cancellation cannot stop another export. */
export async function recordFrames(viewer: ViewerApp, signal: AbortSignal, progress: (n: number) => void, rotationOnly: boolean): Promise<Blob> {
  const encoder = new FFmpeg();
  const abort = () => encoder.terminate();
  signal.addEventListener('abort', abort, { once: true });
  try {
    signal.throwIfAborted();
    await encoder.load({ coreURL, wasmURL, classWorkerURL });
    const count = Math.ceil(viewer.getTimeline().duration * 30);
    for (let i = 0; i < count; i++) {
      signal.throwIfAborted();
      viewer.seekTimeline(i / 30, rotationOnly);
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      const data = viewer.captureScreenshot();
      if (!data) throw new Error('无法采集预演帧');
      const bytes = Uint8Array.from(atob(data.split(',')[1]), c => c.charCodeAt(0));
      await encoder.writeFile(`frame-${String(i).padStart(5, '0')}.png`, bytes);
      progress((i + 1) / count * 0.8);
    }
    encoder.on('progress', ({ progress: n }) => progress(0.8 + Math.max(0, Math.min(1, n)) * 0.2));
    const code = await encoder.exec(['-framerate', '30', '-i', 'frame-%05d.png', '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', 'preview.mp4']);
    signal.throwIfAborted();
    if (code !== 0) throw new Error('FFmpeg 预演编码失败');
    const output = await encoder.readFile('preview.mp4');
    if (typeof output === 'string' || !output.length) throw new Error('FFmpeg 返回空视频');
    return new Blob([new Uint8Array(output)], { type: 'video/mp4' });
  } finally {
    signal.removeEventListener('abort', abort);
    encoder.terminate();
  }
}
