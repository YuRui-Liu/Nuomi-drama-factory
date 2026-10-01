import { describe, expect, it } from 'vitest';
import { cameraStroke } from './camera-stroke';

describe('drawn camera path', () => {
  it('distributes camera times by distance and preserves altitude and look target', () => {
    const keys = cameraStroke([[0, 0, 0], [1, 0, 0], [4, 0, 0]], 2, 8, 3, [0, 1, 0]);
    expect(keys.map(k => k.time)).toEqual([2, 4, 10]);
    expect(keys[1].position).toEqual([1, 3, 0]);
    expect(keys[2].target).toEqual([0, 1, 0]);
  });
  it('rejects a click, repeated points, and an out-of-range duration', () => {
    expect(() => cameraStroke([[0, 0, 0]], 0, 5, 3, [0, 1, 0])).toThrow();
    expect(() => cameraStroke([[0, 0, 0], [0, 0, 0]], 0, 5, 3, [0, 1, 0])).toThrow();
    expect(() => cameraStroke([[0, 0, 0], [1, 0, 0]], 3599, 5, 3, [0, 1, 0])).toThrow();
  });
});
