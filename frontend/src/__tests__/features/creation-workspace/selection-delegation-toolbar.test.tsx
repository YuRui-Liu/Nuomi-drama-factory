import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { SelectionDelegationToolbar } from '@/features/creation-workspace/SelectionDelegationToolbar';
import { useCanvasStore, type CanvasNode } from '@/stores/canvasStore';

vi.mock('@xyflow/react', async original => ({ ...await original<typeof import('@xyflow/react')>(), useViewport: () => ({ x: 0, y: 0, zoom: 1 }), NodeToolbar: ({ children }: { children: ReactNode }) => <div>{children}</div> }));
vi.mock('@/features/direction-references/DirectionReferenceEntry', () => ({ DirectionReferenceEntry: ({ selectedNodes }: { selectedNodes: boolean }) => <button>局部风格:{String(selectedNodes)}</button> }));
beforeEach(() => {
  useCanvasStore.getState().setCanvasData([], []);
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
});
afterEach(() => vi.unstubAllGlobals());
it('appears only for selection and delegates exactly the visible selected ids', () => {
  const delegate = vi.fn();
  render(<SelectionDelegationToolbar project="p" canvasId="c" enabled onDelegate={delegate} />);
  expect(screen.queryByRole('button', { name: /委托/ })).not.toBeInTheDocument();
  act(() => useCanvasStore.getState().setCanvasData([
    { id: 'one', type: 'textAnnotationNode', selected: true, position: { x: 0, y: 0 }, data: {} },
    { id: 'two', type: 'textAnnotationNode', selected: false, position: { x: 0, y: 0 }, data: {} },
  ] as CanvasNode[], []));
  fireEvent.click(screen.getByRole('button', { name: '委托选中节点' }));
  expect(delegate).toHaveBeenCalledWith(['one']);
  expect(screen.getByText('局部风格:true')).toBeInTheDocument();
});
it('hides actions while canvas is unavailable', () => {
  useCanvasStore.getState().setCanvasData([{ id: 'one', selected: true, type: 'textAnnotationNode', position: { x: 0, y: 0 }, data: {} }] as CanvasNode[], []);
  render(<SelectionDelegationToolbar project="p" canvasId="c" enabled={false} onDelegate={vi.fn()} />);
  expect(screen.queryByRole('button', { name: /委托/ })).not.toBeInTheDocument();
});

it('fits the toolbar to the canvas width instead of the wider window', () => {
  useCanvasStore.getState().setCanvasData([{ id: 'one', selected: true, type: 'textAnnotationNode', position: { x: 0, y: 0 }, data: {} }] as CanvasNode[], []);
  const measure = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const width = this.classList.contains('react-flow') ? 260 : Number.parseFloat(this.style.maxWidth) || 380;
    return { left: 100, right: 100 + width, top: 100, bottom: 700, width, height: 40, x: 100, y: 100, toJSON: () => ({}) };
  });
  try {
    render(<div className="react-flow"><SelectionDelegationToolbar project="p" canvasId="c" enabled onDelegate={vi.fn()} /></div>);
    expect(screen.getByRole('toolbar')).toHaveStyle({ maxWidth: '244px' });
  } finally {
    measure.mockRestore();
  }
});

it('keeps selection actions above the bottom workbench and outside the open assistant', () => {
  useCanvasStore.getState().setCanvasData([{ id: 'one', selected: true, type: 'textAnnotationNode', position: { x: 0, y: 0 }, data: {} }] as CanvasNode[], []);
  const measure = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const canvas = this.classList.contains('react-flow');
    const node = this.classList.contains('react-flow__node');
    const left = canvas ? 0 : 700;
    const top = canvas ? 0 : node ? 600 : 650;
    const width = canvas ? 1024 : 340;
    const height = canvas ? 768 : node ? 100 : 40;
    return { left, right: left + width, top, bottom: top + height, width, height, x: left, y: top, toJSON: () => ({}) };
  });
  const size = vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(500);
  try {
    render(<div><main><div className="react-flow"><div className="react-flow__node" data-id="one" /><SelectionDelegationToolbar project="p" canvasId="c" enabled sidePanelOpen onDelegate={vi.fn()} /></div></main><aside data-canvas-chat-dock aria-hidden="false" /></div>);
    expect(screen.getByRole('toolbar')).toHaveStyle({ maxWidth: '476px', transform: 'translate(-556px, -186px)' });
  } finally {
    measure.mockRestore();
    size.mockRestore();
  }
});
