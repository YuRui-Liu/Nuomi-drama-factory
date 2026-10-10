import { afterEach, expect, it, vi } from 'vitest';
import { downloadFile } from './download-file';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('uses a native attachment link after verifying the response, without a blob URL', async () => {
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  const inspect = vi.fn().mockResolvedValue(new Response('x', { headers: { 'content-type': 'application/zip', 'content-disposition': "attachment; filename*=UTF-8''%E5%A4%9C%E7%97%95.zip" } }));
  await downloadFile('/api/v1/project/export', 'fallback.zip', ['application/zip'], inspect);
  expect(click).toHaveBeenCalledOnce();
  expect((click.mock.instances[0] as HTMLAnchorElement).download).toBe('夜痕.zip');
  expect((click.mock.instances[0] as HTMLAnchorElement).getAttribute('href')).toBe('/api/v1/project/export');
});

it('does not download JSON errors as media and can retry after a failure', async () => {
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  const inspect = vi.fn().mockResolvedValueOnce(new Response('{"detail":"素材未就绪"}', { headers: { 'content-type': 'application/json' } }))
    .mockResolvedValueOnce(new Response('video', { headers: { 'content-type': 'video/mp4' } }));
  await expect(downloadFile('/api/video', 'film.mp4', ['video/mp4'], inspect)).rejects.toThrow('素材未就绪');
  expect(click).not.toHaveBeenCalled();
  await downloadFile('/api/video', 'film.mp4', ['video/mp4'], inspect);
  expect(click).toHaveBeenCalledOnce();
});

it('rejects off-origin or unexpected content instead of navigating to it', async () => {
  const inspect = vi.fn();
  await expect(downloadFile('https://other.test/file', 'x.zip', ['application/zip'], inspect)).rejects.toThrow();
  expect(inspect).not.toHaveBeenCalled();
});

it.each([
  [403, 'application/json', '{"detail":"无下载权限"}'],
  [503, 'text/plain', 'unavailable'],
  [200, 'text/html', '<html>sign in</html>'],
])('rejects an invalid HTTP or media response (%s)', async (status, type, body) => {
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  const inspect = vi.fn().mockResolvedValue(new Response(body, { status, headers: { 'content-type': type } }));
  await expect(downloadFile('/api/video', 'film.mp4', ['video/mp4'], inspect)).rejects.toThrow();
  expect(click).not.toHaveBeenCalled();
});

it('checks media with the authenticated cookie and a bounded range request', async () => {
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  const fetcher = vi.fn().mockResolvedValue(new Response('v', { status: 206, headers: { 'content-type': 'video/mp4' } }));
  vi.stubGlobal('fetch', fetcher);
  await downloadFile('/api/video', 'film.mp4', ['video/mp4']);
  expect(fetcher).toHaveBeenCalledWith('/api/video', { credentials: 'include', headers: { Range: 'bytes=0-0' } });
});
