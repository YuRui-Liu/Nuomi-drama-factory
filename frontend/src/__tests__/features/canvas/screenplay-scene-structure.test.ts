import { describe, expect, it } from 'vitest';
import { mergeScreenplayScenes, reorderScreenplayScene, screenplaySceneRanges } from '@/features/canvas/production/screenplaySceneStructureUtils';

describe('screenplay scene text preservation', () => {
  const source = '1-1 门厅 夜\n第一场内容\n\n1-2 楼道 夜\n第二场内容\n\n1-3 屋顶 夜\n第三场内容\n\n1-4 天台 夜\n第四场内容';
  it('merges two early scenes without dropping later scenes', () => {
    const merged = mergeScreenplayScenes(source, screenplaySceneRanges(source), 0)!;
    expect(screenplaySceneRanges(merged)).toHaveLength(3);
    for (const text of ['第一场内容', '第二场内容', '第三场内容', '第四场内容']) {
      expect(merged.split(text)).toHaveLength(2);
    }
    expect(merged).toContain('1-2 屋顶 夜');
    expect(merged).toContain('1-3 天台 夜');
  });
  it('keeps scene headings on new lines when moving the last scene without trailing newline', () => {
    const moved = reorderScreenplayScene(source, screenplaySceneRanges(source), 3, -1)!;
    expect(screenplaySceneRanges(moved)).toHaveLength(4);
    expect(moved.indexOf('第四场内容')).toBeLessThan(moved.indexOf('第三场内容'));
    expect(moved).toContain('1-3 天台 夜');
    expect(moved).toContain('1-4 屋顶 夜');
  });
});
