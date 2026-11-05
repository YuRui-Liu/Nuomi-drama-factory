import { describe, expect, it } from 'vitest';
import { keepFocusVisible, keepNodeVisible } from '@/features/creation-workspace/workspaceLayout';

describe('keepFocusVisible', () => {
  const viewport = { x: 100, y: -80, zoom: 0.75 };
  it('preserves an already visible focus, including both boundaries', () => {
    for (const focus of [24, 200, 776]) expect(keepFocusVisible(viewport, focus, 800)).toBe(viewport);
  });
  it('moves only x by the minimal amount on either edge', () => {
    expect(keepFocusVisible(viewport, -16, 800)).toEqual({ ...viewport, x: 140 });
    expect(keepFocusVisible(viewport, 900, 800)).toEqual({ ...viewport, x: -24 });
  });
  it('ignores unavailable layout and handles a viewport smaller than the margins', () => {
    expect(keepFocusVisible(viewport, 80, 0)).toBe(viewport);
    expect(keepFocusVisible(viewport, NaN, 800)).toBe(viewport);
    expect(keepFocusVisible(viewport, 80, 30)).toEqual({ ...viewport, x: 35 });
  });
});

describe('keepNodeVisible', () => {
  const viewport = { x: 100, y: -80, zoom: 0.75 };
  it('protects full node bounds even when its center is already visible', () => {
    expect(keepNodeVisible(viewport, 600, 900, 800)).toEqual({ ...viewport, x: -24 });
    expect(keepNodeVisible(viewport, -100, 200, 800)).toEqual({ ...viewport, x: 224 });
  });
  it('preserves visible bounds and falls back to center for oversized nodes', () => {
    expect(keepNodeVisible(viewport, 24, 776, 800)).toBe(viewport);
    expect(keepNodeVisible(viewport, -100, 900, 800)).toBe(viewport);
    expect(keepNodeVisible(viewport, 300, 1300, 800)).toEqual({ ...viewport, x: 76 });
  });
});
