import { expect, it } from 'vitest';
import { taskDeepLink } from './task-navigation';
import type { Task } from '@/types/task';

const task = (scope: string, task_type = 'narrative_group_video'): Task => ({ project:'project name', project_id:'p', episode:3, username:'user', task_type, scope, status:'completed', progress:1 });

it('returns to the exact narrative group, including group ids with underscores', () => {
  expect(taskDeepLink(task('group_group_part_2_video_r3'))).toBe('/projects/p/episodes/3/beats?group=group_part_2');
});
it('returns span composition tasks to their group instead of episode composition', () => {
  expect(taskDeepLink(task('group_G2_video_compose_r4_s1', 'narrative_group_video_compose'))).toBe('/projects/p/episodes/3/beats?group=G2');
});
it('keeps malformed narrative task scopes on the real workbench without guessing an id', () => {
  expect(taskDeepLink(task('bad_scope'))).toBe('/projects/p/episodes/3/beats');
});
