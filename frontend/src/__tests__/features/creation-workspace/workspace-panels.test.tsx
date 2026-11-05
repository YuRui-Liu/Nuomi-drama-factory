import { useEffect, useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { WorkspaceAgentPanel } from '@/features/creation-workspace/WorkspacePanels';
import { CanvasWorkspaceSwitcher } from '@/features/creation-workspace/CanvasWorkspaceSwitcher';

vi.mock('@/hooks/use-media-query', () => ({ useMediaQuery: () => true }));
afterEach(cleanup);

it('keeps the same conversation and draft mounted across closing and breakpoints', () => {
  const unmount = vi.fn();
  function Session() {
    const [draft, setDraft] = useState('');
    useEffect(() => unmount, []);
    return <input aria-label="draft" value={draft} onChange={e => setDraft(e.target.value)} />;
  }
  const panel = (open: boolean, desktop = true, managed = true) => <WorkspaceAgentPanel open={open} desktop={desktop} managed={managed} visible={open} title="Agent" description=""><Session /></WorkspaceAgentPanel>;
  const { rerender } = render(panel(true));
  const aside = screen.getByRole('complementary');
  expect(aside).toHaveStyle({ width: '380px' });
  expect(aside).not.toHaveClass('absolute');
  fireEvent.change(screen.getByLabelText('draft'), { target: { value: '未发送的任务' } });
  rerender(panel(false));
  expect(aside).toHaveAttribute('hidden');
  expect(aside).toHaveAttribute('inert');
  expect(unmount).not.toHaveBeenCalled();
  rerender(panel(true, false));
  expect(screen.getByLabelText('draft')).toHaveValue('未发送的任务');
  expect(aside).toHaveClass('absolute');
  rerender(panel(true, true, false));
  expect(aside).toHaveStyle({ maxWidth: 'calc(100vw - 360px)' });
  expect(unmount).not.toHaveBeenCalled();
});

it('reclaims the left rail width and preserves search and creation draft on reopen', async () => {
  render(<CanvasWorkspaceSwitcher canvases={[]} pending={false} onCreate={vi.fn()} onSelect={vi.fn()} />);
  const aside = screen.getByRole('complementary');
  expect(aside).toHaveClass('w-[216px]', 'shrink-0');
  expect(screen.queryByLabelText('画布名称')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('搜索画布'), { target: { value: '分集' } });
  fireEvent.click(screen.getByRole('button', { name: '新建画布' }));
  fireEvent.change(screen.getByLabelText('画布名称'), { target: { value: '新草稿' } });
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: '收起项目画布栏' }));
  expect(aside).toHaveAttribute('hidden');
  expect(aside).toHaveAttribute('inert');
  fireEvent.click(screen.getByRole('button', { name: '展开项目画布' }));
  expect(screen.getByLabelText('搜索画布')).toHaveValue('分集');
  fireEvent.click(screen.getByRole('button', { name: '新建画布' }));
  expect(screen.getByLabelText('画布名称')).toHaveValue('新草稿');
});
