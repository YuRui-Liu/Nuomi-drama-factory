/** Use the browser's attachment download, keeping large media out of JS memory. */
export async function downloadFile(
  url: string,
  fallbackName: string,
  contentTypes: string[],
  inspect: (url: string) => Promise<Response> = (target) => fetch(target, {
    credentials: 'include', headers: { Range: 'bytes=0-0' },
  }),
): Promise<void> {
  const target = new URL(url, window.location.origin);
  if (target.origin !== window.location.origin || !['http:', 'https:'].includes(target.protocol)) {
    throw new Error('下载地址必须属于当前项目服务');
  }
  const response = await inspect(target.pathname + target.search);
  const type = response.headers.get('content-type')?.split(';')[0].trim().toLowerCase() ?? '';
  if (!response.ok || type.includes('json')) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === 'string' ? body.detail : typeof body?.error === 'string' ? body.error : `下载失败（${response.status}），请重试`);
  }
  await response.body?.cancel();
  if (!contentTypes.includes(type)) throw new Error('下载内容格式不符，请重试');
  const disposition = response.headers.get('content-disposition') ?? '';
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1];
  let name = /filename="([^"]+)"/i.exec(disposition)?.[1] ?? fallbackName;
  if (encoded) { try { name = decodeURIComponent(encoded); } catch { /* Use safe fallback. */ } }
  name = name.replace(/[\\/\r\n\0]/g, '_');
  const link = document.createElement('a');
  link.href = target.pathname + target.search;
  link.download = name;
  document.body.appendChild(link);
  try { link.click(); } finally { link.remove(); }
  // The browser owns saving now. This is not confirmation of disk delivery.
}
