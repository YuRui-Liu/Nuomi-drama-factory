import { describe, expect, it } from 'vitest';
import { directorStageLabel } from '@/features/canvas/director/directorStatus';

const translate = (_key: string, options: { defaultValue: string }) => options.defaultValue;

describe('director stage labels', () => {
  it('maps every backend stage to a readable localized label', () => {
    for (const stage of ['created', 'optimizing', 'preparing', 'submitting', 'queued', 'generating', 'completed', 'failed', 'submission_unknown']) {
      expect(directorStageLabel(stage, translate)).not.toBe(stage);
    }
    expect(directorStageLabel('submission_unknown', translate)).toContain('未知');
  });
});
