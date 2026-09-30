import { expect, it } from 'vitest';
import { pinnedResourceTitle, resourceTitle } from './resource-title';

it('derives a readable title without changing identity', () => {
  expect(resourceTitle({ id: 'uuid', kind: 'skill', content: '\n  ## 编剧创作技法 ###\n正文' })).toBe('编剧创作技法');
  expect(resourceTitle({ id: 'uuid', kind: 'reference', content: '\n' })).toBe('参考资料 · uuid');
  expect(resourceTitle({ id: 'uuid', kind: 'prompt', content: '很'.repeat(60) })).toHaveLength(49);
});

it('never labels an old pin using a newer revision title', () => {
  const resource = { id: 'uuid', revision: 2, content: '# 新版标题', kind: 'skill' as const, owner: 'me', content_hash: 'hash', archived: false };
  expect(pinnedResourceTitle([resource], { id: 'uuid', revision: 1 })).toBe('uuid');
  expect(pinnedResourceTitle([resource], { id: 'uuid', revision: 2 })).toBe('新版标题');
});
