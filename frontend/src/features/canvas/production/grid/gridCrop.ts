export interface GridCellCropGeometry {
  cellIndex: number;
  row: number;
  col: number;
  crop: { x: number; y: number; width: number; height: number };
}

/** Shared by the visible grid overlay, provenance, and actual pixel extraction. */
export function getGridCellCropGeometry(
  imageWidth: number,
  imageHeight: number,
  rows: number,
  cols: number,
  separatorPx = 0,
): GridCellCropGeometry[] {
  if (![imageWidth, imageHeight, rows, cols].every(value => Number.isSafeInteger(value) && value > 0)
    || !Number.isSafeInteger(separatorPx) || separatorPx < 0) {
    throw new Error('图片尺寸、宫格行列与分隔线必须是有效像素整数');
  }
  const usableWidth = imageWidth - (cols - 1) * separatorPx;
  const usableHeight = imageHeight - (rows - 1) * separatorPx;
  if (usableWidth < cols || usableHeight < rows) throw new Error('分隔线过宽，无法保留有效画面');
  // Match SplitStoryboardToolEditor: allocate remainder pixels to leading cells.
  const split = (total: number, count: number) => Array.from({ length: count }, (_, index) =>
    Math.floor(total / count) + (index < total % count ? 1 : 0));
  const widths = split(usableWidth, cols);
  const heights = split(usableHeight, rows);
  const cells: GridCellCropGeometry[] = [];
  let y = 0;
  for (let row = 0; row < rows; row += 1) {
    let x = 0;
    for (let col = 0; col < cols; col += 1) {
      cells.push({ cellIndex: row * cols + col, row, col, crop: { x, y, width: widths[col], height: heights[row] } });
      x += widths[col] + separatorPx;
    }
    y += heights[row] + separatorPx;
  }
  return cells;
}
