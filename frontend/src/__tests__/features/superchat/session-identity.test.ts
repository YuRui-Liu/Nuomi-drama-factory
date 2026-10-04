import { describe, expect, it } from 'vitest';
import { belongsToSession } from '@/features/superchat/session-identity';

describe('assistant session generation', () => {
  it('rejects events from a cleared session', () => {
    expect(belongsToSession({ session_id: 'new', generation: 2 }, { session_id: 'old', generation: 1 })).toBe(false);
    expect(belongsToSession({ session_id: 'new', generation: 2 }, { session_id: 'new', generation: 2 })).toBe(true);
  });
});
