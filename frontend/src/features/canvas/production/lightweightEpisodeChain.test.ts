import { describe, expect, it } from 'vitest';
import { buildStoryboardGroups, reconcileStoryboardReferenceEdges, storyboardFrame } from './lightweightEpisodeChain';

describe('lightweight episode storyboard groups', () => {
  it('removes obsolete automatic image links but preserves manual and other-source links', () => {
    const manual = { id: 'manual', source: 'manual-asset', target: 'grid' };
    const unrelated = { id: 'other', source: 'other-asset', target: 'grid', data: { lightweightManagedReferenceForSourceNodeId: 'another-table' } };
    const initial = reconcileStoryboardReferenceEdges([manual, unrelated], 'table', 'grid', ['old', 'manual-asset']);
    expect(initial.find(edge => edge.source === 'old')?.data?.lightweightManagedReferenceForSourceNodeId).toBe('table');
    expect(initial.find(edge => edge.source === 'manual-asset')).toBe(manual);
    const next = reconcileStoryboardReferenceEdges(initial, 'table', 'grid', ['new']);
    expect(next.some(edge => edge.source === 'old')).toBe(false);
    expect(next).toContain(manual);
    expect(next).toContain(unrelated);
    expect(next.some(edge => edge.source === 'new')).toBe(true);
    expect(reconcileStoryboardReferenceEdges(next, 'table', 'grid', ['new'])).toBe(next);
  });
  it('preserves scene boundaries and ordered shots without padding imaginary shots', () => {
    const groups = buildStoryboardGroups([
      ...Array.from({ length: 5 }, (_, i) => ({ shot_no: i + 1, scene_tags: '门厅', visual_description: `动作${i}` })),
      { shot_no: 6, scene_tags: '街道', visual_description: '走向街道' },
    ]);
    expect(groups.map(group => group.frames.length)).toEqual([4, 1, 1]);
    expect(groups.map(group => [group.gridRows, group.gridCols])).toEqual([[2, 2], [1, 1], [1, 1]]);
    expect(groups.flatMap(group => group.frames).map(frame => frame.id)).toEqual(Array.from({ length: 6 }, (_, i) => `shot-${i + 1}`));
  });
  it.each([
    [1, 1, 1],
    [1, 2, 2],
    [1, 3, 3],
    [2, 2, 4],
  ])('uses an unambiguous %sx%s grid for %s shot(s)', (rows, cols, count) => {
    const [group] = buildStoryboardGroups(Array.from({ length: count }, (_, i) => ({
      shot_no: i + 1, scene_tags: '门厅', visual_description: `动作${i}`,
    })));
    expect(group).toMatchObject({ gridRows: rows, gridCols: cols });
    expect(group.frames).toHaveLength(count);
  });
  it('groups descriptive scene-tag variations by their stable first scene token, in order and within four cells', () => {
    const tags = [
      '居民楼门厅、暴雨夜、停电、旧门灯、湿地面',
      '居民楼门厅、旧门灯、雨夜',
      '居民楼门厅、停电、旧门灯、积水',
    ];
    const rows = Array.from({ length: 17 }, (_, i) => ({
      shot_no: i + 1,
      scene_tags: tags[i % tags.length],
      visual_description: `第${i + 1}镜`,
    }));
    const groups = buildStoryboardGroups(rows);

    expect(groups.map(group => group.frames.length)).toEqual([4, 4, 4, 4, 1]);
    expect(groups.flatMap(group => group.frames).map(frame => frame.id))
      .toEqual(Array.from({ length: 17 }, (_, i) => `shot-${i + 1}`));
    expect(groups.every(group => group.frames.length <= 4)).toBe(true);
  });
  it('prefers a stable explicit scene id over descriptive per-shot tags', () => {
    const groups = buildStoryboardGroups([
      { shot_no: 1, scene_id: 'lobby', scene_tags: '居民楼门厅、暴雨夜', visual_description: '进门' },
      { shot_no: 2, scene_id: 'lobby', scene_tags: '居民楼门厅、停电', visual_description: '查看灯' },
      { shot_no: 3, scene_id: 'stairwell', scene_tags: '居民楼门厅、停电', visual_description: '走上楼梯' },
    ]);

    expect(groups.map(group => group.frames.length)).toEqual([2, 1]);
  });
  it('keeps explicit narrative groups distinct and caps provider grid inputs at four', () => {
    const groups = buildStoryboardGroups(Array.from({ length: 9 }, (_, i) => ({
      shot_no: i + 1, narrative_group: i < 7 ? '等待' : '重逢', scene_tags: '门厅', visual_description: '等待',
    })));
    expect(groups.map(group => group.frames.length)).toEqual([4, 3, 2]);
    expect(groups.flatMap(group => group.frames).map(frame => frame.id)).toEqual(Array.from({ length: 9 }, (_, i) => `shot-${i + 1}`));
    expect(groups.every(group => group.frames.length <= 4)).toBe(true);
  });
  it('carries professional shot instructions and dialogue into actual downstream descriptions', () => {
    const frame = storyboardFrame({ visual_description: '两人对话', action: '左侧回头', emotion: '担忧',
      lighting_mood: '窗侧冷光', dialogue: '你回来了', video_motion_prompt: '缓慢推进', duration: '6秒' }, 0);
    expect(frame.description).toContain('动作与站位：左侧回头');
    expect(frame.description).toContain('表情：担忧');
    expect(frame.description).toContain('灯光：窗侧冷光');
    expect(frame.description).toContain('对白：你回来了');
    expect(frame.description).toContain('运镜与动态：缓慢推进');
    expect(frame.duration_seconds).toBe(6);
  });
  it('does not fabricate durations or content for absent shots', () => {
    expect(buildStoryboardGroups([])).toEqual([]);
    expect(storyboardFrame({ duration: 'unknown', visual_description: '空镜' }, 0).duration_seconds).toBeUndefined();
  });
});
