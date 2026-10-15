import { describe, expect, it } from 'vitest';
import { emptyTimeline, pathKeys, sampleKeys, upsertKey, validateTimeline, type ObjectKey } from '@/features/viewer-kit/three-d/sceneTimeline';
const key = (time: number, x: number, yawDeg = 0): ObjectKey => ({ time, position: [x, 0, 0], yawDeg, scale: [1, 1, 1] });
describe('scene timeline', () => {
  it('samples transforms deterministically, with shortest angle interpolation and endpoint clamps', () => {
    const keys = [key(0, 0, 350), key(4, 8, 10)];
    expect(sampleKeys(keys, 2)).toEqual(key(2, 4, 360));
    expect(sampleKeys(keys, -1)).toEqual(keys[0]);
    expect(sampleKeys(keys, 8)).toEqual(keys[1]);
    expect(sampleKeys(keys, 2)).toEqual(sampleKeys(keys, 2));
  });
  it('replaces an exact key without duplicate times', () => {
    expect(upsertKey([key(0, 0), key(2, 1)], key(2, 8))).toEqual([key(0, 0), key(2, 8)]);
  });
  it('rejects invalid time, duplicate keys, overlapping clips and nonfinite transforms', () => {
    for (const keys of [[key(0, 0), key(0, 1)], [key(6, 0)], [key(0, NaN)], [key(Infinity, 0)]]) {
      expect(() => validateTimeline({ ...emptyTimeline(), tracks: { stable: { keys, clips: [] } } })).toThrow();
    }
    expect(() => validateTimeline({ ...emptyTimeline(), tracks: { stable: { keys: [], clips: [{ start: 0, end: 3, speed: 1, pose: 'walking' }, { start: 2, end: 4, speed: 1, pose: 'running' }] } } })).toThrow();
  });
  it('times freehand paths by distance and removes identical samples', () => {
    const keys = pathKeys([[0, 0, 0], [0, 0, 0], [1, 0, 0], [4, 0, 0]], 1, 5, key(0, 0));
    expect(keys.map(k => k.time)).toEqual([1, 2, 5]);
    validateTimeline({ ...emptyTimeline(), tracks: { actor: { keys, clips: [] } } });
  });
  it('accepts a legacy empty timeline and JSON roundtrip with stable object keys', () => {
    const timeline = { ...emptyTimeline(), tracks: { 'actor-stable-id': { keys: [key(0, 2)], clips: [] } } };
    const restored = JSON.parse(JSON.stringify(timeline));
    expect(() => validateTimeline(restored)).not.toThrow();
    expect(restored.tracks['actor-stable-id'].keys[0].position).toEqual([2, 0, 0]);
  });
});
