import { act, fireEvent, render, screen } from '@testing-library/react';
import { vi, it, expect } from 'vitest';
import { StudioUnsavedGuard } from './studio-unsaved';
const mocks = vi.hoisted(() => ({ proceed: vi.fn(), reset: vi.fn(), opts: null as any }));
vi.mock('@tanstack/react-router', () => ({ useBlocker: (opts: any) => { mocks.opts = opts; return {status:'blocked', proceed:mocks.proceed, reset:mocks.reset}; } }));

it('waits for a successful save before proceeding, and allows keeping edits', () => {
  render(<StudioUnsavedGuard module="intro" />);
  act(() => { window.dispatchEvent(new CustomEvent('studio-dirty',{detail:{module:'intro',dirty:true}})); });
  expect(mocks.opts.shouldBlockFn()).toBe(true);
  const save = vi.fn(); window.addEventListener('studio-save-intro', save);
  fireEvent.click(screen.getByRole('button',{name:'保存后离开'}));
  expect(save).toHaveBeenCalledOnce();
  expect(mocks.proceed).not.toHaveBeenCalled();
  act(() => { window.dispatchEvent(new CustomEvent('studio-dirty',{detail:{module:'intro',dirty:false}})); });
  expect(mocks.proceed).toHaveBeenCalledOnce();
  window.removeEventListener('studio-save-intro',save);
});
