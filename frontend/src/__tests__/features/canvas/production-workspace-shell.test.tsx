import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { ReactFlowProvider } from '@xyflow/react';
import { ProductionNodeShell } from '@/features/canvas/production/ProductionNodeShell';
import { ProductionWorkspaceLayout } from '@/features/canvas/production/workspace/ProductionWorkspaceLayout';

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
});

function renderWorkspace(beforeClose = vi.fn(() => true)) {
  return render(<ReactFlowProvider>
    <ProductionNodeShell
      title="E01 · 专业剧本"
      summary="已采用当前正文"
      status="已采用 · 第 3 版"
      actionLabel="阅读剧本"
      workspace
      beforeClose={beforeClose}
    >
      {({ requestClose }) => (
        <ProductionWorkspaceLayout
          title="E01 · 专业剧本"
          onBack={requestClose}
          navigation={<button type="button">专业剧本</button>}
          actions={<button type="button">预览差异并交给导演</button>}
        >
          <p>正文仍在这里。</p>
        </ProductionWorkspaceLayout>
      )}
    </ProductionNodeShell>
  </ReactFlowProvider>);
}

it('exposes the node state and a named primary entry, then opens a navigable workspace', async () => {
  renderWorkspace();

  expect(screen.getByRole('status')).toHaveTextContent('已采用 · 第 3 版');
  const entry = screen.getByRole('button', { name: '阅读剧本' });
  fireEvent.click(entry);

  const dialog = await screen.findByRole('dialog');
  expect(within(dialog).getByRole('button', { name: '返回画布' })).toBeInTheDocument();
  expect(within(dialog).getByRole('navigation', { name: '工作区导航' })).toHaveTextContent('专业剧本');
  expect(within(dialog).getByRole('main')).toHaveTextContent('正文仍在这里。');
  expect(within(dialog).getByRole('contentinfo')).toHaveTextContent('预览差异并交给导演');

  fireEvent.click(within(dialog).getByRole('button', { name: '返回画布' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  await waitFor(() => expect(entry).toHaveFocus());
});

it('keeps the workspace open when the close guard rejects returning to the canvas', async () => {
  const beforeClose = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
  renderWorkspace(beforeClose);

  fireEvent.click(screen.getByRole('button', { name: '阅读剧本' }));
  const dialog = await screen.findByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: '返回画布' }));

  expect(beforeClose).toHaveBeenCalledTimes(1);
  expect(within(dialog).getByRole('main')).toHaveTextContent('正文仍在这里。');

  fireEvent.click(within(dialog).getByRole('button', { name: '返回画布' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});

it('keeps the existing compact dialog for ordinary nodes', async () => {
  render(<ReactFlowProvider>
    <ProductionNodeShell title="普通节点" summary="现有摘要" actionLabel="编辑普通节点">
      <p>旧版节点内容。</p>
    </ProductionNodeShell>
  </ReactFlowProvider>);

  fireEvent.click(screen.getByRole('button', { name: '编辑普通节点' }));
  const dialog = await screen.findByRole('dialog');
  expect(dialog).toHaveClass('max-h-[90vh]');
  expect(within(dialog).getByText('旧版节点内容。')).toBeInTheDocument();
});
