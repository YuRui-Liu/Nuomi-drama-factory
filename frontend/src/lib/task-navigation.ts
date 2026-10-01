import { stageForTaskType } from './episode-stage-registry';
import { TASK_TYPES } from './task-types';
import type { Task } from '@/types/task';

export function taskDeepLink(task: Task): string | null {
  const { episode, task_type } = task;
  const project = encodeURIComponent(task.project_id ?? task.project);

  // Project-level tasks (no episode)
  if (task_type === TASK_TYPES.BUILD_CHARACTERS) {
    return `/projects/${project}/characters`;
  }
  if (task_type === TASK_TYPES.INGEST_FAST) {
    return `/projects/${project}/ingest`;
  }
  if (task_type === TASK_TYPES.BUILD_EPISODES) {
    return `/projects/${project}/episodes`;
  }
  if (task_type === TASK_TYPES.CHARACTER_PORTRAIT) {
    return `/projects/${project}/characters`;
  }
  if (
    task_type === TASK_TYPES.IDENTITY_IMAGE ||
    task_type === TASK_TYPES.IDENTITY_PORTRAIT
  ) {
    return `/projects/${project}/characters`;
  }

  if (!episode || episode <= 0) return null;

  const base = `/projects/${project}/episodes/${episode}`;
  if (task_type.startsWith('narrative_group_') || task_type === 'narrative_storyboard_repair') {
    const group = task.scope?.match(/^group_(.+)_(?:sketch|render|video(?:_compose)?)_r\d+(?:_s\d+)?$/)?.[1];
    return `${base}/beats${group ? `?${new URLSearchParams({group})}` : ''}`;
  }
  const stage = stageForTaskType(task_type);
  if (stage) return `${base}${stage.routeSegment}`;
  return base; // unknown task type — at least land on the episode shell
}
