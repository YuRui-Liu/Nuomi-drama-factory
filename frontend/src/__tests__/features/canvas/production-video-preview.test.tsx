import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { ProductionVideoPreview } from '@/features/canvas/nodes/ProductionVideoPreview';

it('opens the saved video in a full-size dialog without starting generation', () => {
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  render(<ProductionVideoPreview src="/static/saved.mp4" title="E1 环境声预览" />);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '预览视频' }));
  expect(pause).toHaveBeenCalledOnce();
  const dialog = screen.getByRole('dialog', { name: 'E1 环境声预览' });
  expect(dialog.querySelector('video')).toHaveAttribute('src', '/static/saved.mp4');
  expect(dialog.querySelector('video')).toHaveAttribute('controls');
  expect(dialog.querySelector('video')).not.toHaveAttribute('muted');
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  pause.mockRestore();
});
