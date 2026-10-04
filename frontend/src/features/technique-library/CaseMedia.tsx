import { useState } from 'react';

export function CaseMedia({ url }: { url?: string | null }) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  if (!url) return null;
  let path: string;
  try {
    const parsed = new URL(url);
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) return null;
    path = parsed.pathname;
  } catch { return null; }
  const video = /\.(mp4|webm|ogv)$/i.test(path);
  const image = /\.(png|jpe?g|webp|gif)$/i.test(path);
  const failed = failedUrl === url;
  return <section aria-label="案例媒体" className="space-y-2">
    {!failed && video && <video key={url} className="max-h-96 w-full rounded-lg bg-black" controls preload="none" src={url} onError={() => setFailedUrl(url)} />}
    {!failed && image && <img className="max-h-96 w-full rounded-lg object-contain" src={url} alt="案例参考画面" loading="lazy" onError={() => setFailedUrl(url)} />}
    {failed && <p role="status" className="text-xs text-amber-200">预览加载失败，可打开媒体来源查看。</p>}
    <a className="text-xs text-cyan-300 underline" href={url} target="_blank" rel="noopener noreferrer">打开媒体来源 ↗</a>
  </section>;
}
