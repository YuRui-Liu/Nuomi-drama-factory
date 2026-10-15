import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { DirectorLibrary, builtinDirectors, filterDirectors } from './director-library';
import { emptyDirectorConfig } from './director-config';
const mocks = vi.hoisted(() => ({ post: vi.fn(), put: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: { get: () => ({ json: async () => ({ data: [] }) }), post: mocks.post, put: mocks.put } }));
function setup(dirty = false) {
  const choose = vi.fn();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><DirectorLibrary name="当前导演" config={{ ...emptyDirectorConfig, camera_motion: '我的缓推', allow_adaptation: true }} dirty={dirty} onChoose={choose} /></QueryClientProvider>);
  return choose;
}
beforeEach(() => vi.clearAllMocks());
describe('personal director library', () => {
  it('combines source, case-insensitive name, genre and any selected tag', () => {
    expect(filterDirectors(builtinDirectors, 'builtin', 'tvc', '广告', ['产品特写', '不存在'])).toHaveLength(1);
    expect(filterDirectors(builtinDirectors, 'mine', '', '', [])).toHaveLength(0);
    expect(builtinDirectors.some(card => card.creative_types.includes('动作'))).toBe(true);
    expect(builtinDirectors.some(card => card.creative_types.includes('情感'))).toBe(true);
  });
  it('selection only fills editor and filtering does not choose', () => {
    const choose = setup();
    fireEvent.change(screen.getByLabelText('搜索导演名称'), { target: { value: 'TVC' } });
    expect(choose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'TVC 广告' }));
    expect(choose).toHaveBeenCalledOnce();
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it('offers cancel and discard before switching unsaved edits', () => {
    const choose = setup(true);
    fireEvent.click(screen.getByRole('button', { name: 'TVC 广告' }));
    fireEvent.click(screen.getByRole('button', { name: /^取消$/ }));
    expect(choose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'TVC 广告' }));
    fireEvent.click(screen.getByRole('button', { name: '放弃修改并切换' }));
    expect(choose).toHaveBeenCalledOnce();
  });
  it('keeps the form and editor when saving fails; switches only after successful save', async () => {
    const choose = setup(true);
    mocks.post.mockRejectedValueOnce(new Error('offline'));
    fireEvent.click(screen.getByRole('button', { name: 'TVC 广告' }));
    fireEvent.click(screen.getByRole('button', { name: '另存后切换' }));
    fireEvent.click(screen.getByRole('button', { name: '确认另存收藏' }));
    await screen.findByText(/收藏失败/);
    expect(choose).not.toHaveBeenCalled();
    expect(screen.getByLabelText('收藏名称')).toHaveValue('当前导演');
    mocks.post.mockResolvedValueOnce(new Response(JSON.stringify({ data: { ...builtinDirectors[0], id: 'personal', builtin: false } }), { status: 201 }));
    fireEvent.click(screen.getByRole('button', { name: '确认另存收藏' }));
    await waitFor(() => expect(choose).toHaveBeenCalledOnce());
    const submitted = mocks.post.mock.calls[1][1].json;
    expect(submitted.preferences.camera_motion).toBe('我的缓推');
    expect(submitted.preferences).not.toHaveProperty('allow_adaptation');
    expect(submitted).not.toHaveProperty('source_text');
  });
});
