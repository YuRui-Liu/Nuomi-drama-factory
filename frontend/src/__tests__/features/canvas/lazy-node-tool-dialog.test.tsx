import type { ComponentType } from 'react';
import { act, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasStore } from '@/stores/canvasStore';

const deferredModule = vi.hoisted(() => {
  let resolveModule!: (module: { NodeToolDialog: ComponentType }) => void;
  const promise = new Promise<{ NodeToolDialog: ComponentType }>((resolve) => {
    resolveModule = resolve;
  });

  return { load: vi.fn(() => promise), resolveModule };
});

vi.mock('@/features/canvas/ui/NodeToolDialog', () => deferredModule.load());

import { LazyNodeToolDialog } from '@/features/canvas/ui/LazyNodeToolDialog';

describe('LazyNodeToolDialog', () => {
  beforeEach(() => {
    useCanvasStore.setState({ activeToolDialog: null });
    deferredModule.load.mockClear();
  });

  it('hides the loading overlay when closed and keeps the loaded container mounted', async () => {
    render(<LazyNodeToolDialog />);
    expect(deferredModule.load).not.toHaveBeenCalled();

    act(() => {
      useCanvasStore.setState({
        activeToolDialog: { nodeId: 'node-1', toolType: 'crop' },
      });
    });
    expect(await screen.findByRole('status')).toHaveAttribute('aria-live', 'polite');

    act(() => {
      useCanvasStore.setState({ activeToolDialog: null });
    });
    expect(screen.queryByRole('status')).not.toBeInTheDocument();

    deferredModule.resolveModule({
      NodeToolDialog: () => <div>node tool loaded</div>,
    });
    expect(await screen.findByText('node tool loaded')).toBeInTheDocument();

    expect(screen.getByText('node tool loaded')).toBeInTheDocument();
  });
});
