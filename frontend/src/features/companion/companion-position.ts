/** Match Header's 56px desktop / 96px two-row narrow layout, plus a gap. */
export function companionTopPx(requested: number, width: number, height: number): number {
  const minimum = (width >= 1024 ? 56 : 96) + 8;
  return Math.max(minimum, Math.min(requested, height - 80));
}
