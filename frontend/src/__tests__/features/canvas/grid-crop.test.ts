import { describe, expect, it, vi } from 'vitest';
import { getGridCellCropGeometry } from '@/features/canvas/production/grid/gridCrop';
import { splitImageSource } from '@/commands/image';

describe('grid crop pixel geometry', () => {
  it('distributes remainder pixels without dropping the image edge', () => {
    const cells = getGridCellCropGeometry(101, 53, 2, 2);
    expect(cells.map(cell => cell.crop)).toEqual([
      { x: 0, y: 0, width: 51, height: 27 }, { x: 51, y: 0, width: 50, height: 27 },
      { x: 0, y: 27, width: 51, height: 26 }, { x: 51, y: 27, width: 50, height: 26 },
    ]);
  });
  it('excludes separators and keeps row-major positions', () => {
    const cells = getGridCellCropGeometry(103, 55, 2, 2, 3);
    expect(cells[3]).toEqual({ cellIndex: 3, row: 1, col: 1, crop: { x: 53, y: 29, width: 50, height: 26 } });
  });
  it.each([[0, 4, 1, 1, 0], [4, 4, 0, 1, 0], [4, 4, 2, 2, 3], [4, 4, 1.5, 1, 0], [4, 4, 1, 1, -1]])(
    'rejects invalid dimensions/layout %s %s %s %s %s', (...args) => {
      expect(() => getGridCellCropGeometry(...args as [number, number, number, number, number])).toThrow();
    },
  );
  it('draws exactly the same source rectangles through the image gateway', async () => {
    const drawImage = vi.fn();
    const original = document.createElement.bind(document);
    const create = vi.spyOn(document, 'createElement').mockImplementation((tag, options) => {
      if (tag === 'canvas') return { width: 0, height: 0, getContext: () => ({ drawImage }), toDataURL: () => 'data:image/png;base64,fixture' } as unknown as HTMLCanvasElement;
      return original(tag, options);
    });
    vi.stubGlobal('Image', class {
      naturalWidth = 103; naturalHeight = 55; onload: (() => void) | null = null;
      set src(_value: string) { queueMicrotask(() => this.onload?.()); }
    });
    try {
      const outputs = await splitImageSource('/fixture.png', 2, 2, 3);
      expect(outputs).toHaveLength(4);
      const expected = getGridCellCropGeometry(103, 55, 2, 2, 3);
      expect(drawImage.mock.calls.map(call => call.slice(1, 5))).toEqual(expected.map(({ crop }) => [crop.x, crop.y, crop.width, crop.height]));
    } finally { create.mockRestore(); vi.unstubAllGlobals(); }
  });
});
