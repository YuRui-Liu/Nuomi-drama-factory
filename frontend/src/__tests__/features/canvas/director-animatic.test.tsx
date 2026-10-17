import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { DirectorShotViews } from '@/features/canvas/production/DirectorShotViews';

const groups = [{ id: 'g1', shots: [{ id: 's1', duration_seconds: 2, action: '开门' }, { id: 's2', duration_seconds: 3, action: '转身' }] }];
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('reports delayed playback rejection after the timeline position changes', async () => {
  let rejectPlay!: (error: Error) => void;
  const pending = new Promise<void>((_, reject) => { rejectPlay = reject; });
  let paused = true;
  vi.spyOn(HTMLMediaElement.prototype, 'paused', 'get').mockImplementation(() => paused);
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => { paused = false; return pending; });
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => { paused = true; });
  render(<DirectorShotViews groups={groups} view="animatic" mediaGroups={[{ id: 'g1', stages: { video: { original_audio_path: '/original.wav' } } }]} />);
  fireEvent.click(screen.getByRole('button', { name: '播放预演' }));
  fireEvent.change(screen.getByRole('slider', { name: '预演时间' }), { target: { value: '1' } });
  await act(async () => { rejectPlay(new Error('decode failed')); await Promise.resolve(); });
  expect(screen.getByRole('alert')).toHaveTextContent('已有音轨无法播放');
  expect(screen.getByRole('button', { name: '播放预演' })).toBeInTheDocument();
});

it('stops the captured audio player when switching to the shot table', () => {
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
  const mediaGroups = [{ id: 'g1', stages: { video: { original_audio_path: '/original.wav' } } }];
  const result = render(<DirectorShotViews groups={groups} view="animatic" mediaGroups={mediaGroups} />);
  fireEvent.click(screen.getByRole('button', { name: '播放预演' }));
  pause.mockClear();
  result.rerender(<DirectorShotViews groups={groups} view="table" mediaGroups={mediaGroups} />);
  expect(pause).toHaveBeenCalled();
  result.rerender(<DirectorShotViews groups={groups} view="animatic" mediaGroups={mediaGroups} />);
  expect(screen.getByRole('button', { name: '播放预演' })).toBeInTheDocument();
});

it('shows an existing group board with its scope and stops on a broken image', () => {
  render(<DirectorShotViews groups={groups} view="animatic" mediaGroups={[{ id: 'g1', stages: { render: { grid_asset: '/existing-board.png' } } }]} />);
  const image = screen.getByRole('img', { name: 'g1 整组分镜参考' });
  expect(image).toHaveAttribute('src', '/existing-board.png');
  fireEvent.click(screen.getByRole('button', { name: '播放预演' }));
  fireEvent.error(image);
  expect(screen.getByRole('alert')).toHaveTextContent('分镜图片无法读取');
  expect(screen.getByRole('button', { name: '播放预演' })).toBeInTheDocument();
});

it('never presents media from an outdated plan as current preview', () => {
  render(<DirectorShotViews groups={groups} view="animatic" mediaGroups={[{ id: 'g1', stages: { render: { grid_asset: '/old.png', needs_regeneration: true } } }]} />);
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  expect(screen.getByText(/分镜来源已更新/)).toBeInTheDocument();
});

it('seeks the existing group audio and stops it when the preview is removed', () => {
  const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
  const view = render(<DirectorShotViews groups={groups} view="animatic" mediaGroups={[{ id: 'g1', stages: { video: { original_audio_path: '/original.wav' } } }]} />);
  const audio = view.container.querySelector('audio')!;
  fireEvent.click(screen.getByRole('button', { name: '播放预演' }));
  expect(play).toHaveBeenCalled();
  fireEvent.change(screen.getByRole('slider', { name: '预演时间' }), { target: { value: '3' } });
  expect(audio.currentTime).toBe(3);
  pause.mockClear();
  view.unmount();
  expect(pause).toHaveBeenCalled();
});
