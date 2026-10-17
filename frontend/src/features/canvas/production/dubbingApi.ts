import { apiCall } from '@/api/client';

export interface DubbingVoice {
  character_id: string;
  identity_id: string;
  version_id: string;
}

export type DubbingPerformanceMode = 'neutral' | 'emotion_text';

export interface DubbingPerformanceInput {
  mode: DubbingPerformanceMode;
  direction: string;
  payload?: string;
}

export interface DubbingDialogue {
  dialogue_id: string;
  character_id: string;
  identity_id: string;
  speaker: string;
  script_revision: string;
  source_revision: string;
  group_id: string;
  text: string;
  voice_version_id: string;
  target_duration_seconds: number | null;
  performance: Record<string, unknown>;
  source_key: string;
  source_status: 'active' | 'deleted' | 'needs_review';
  input_hash?: string;
  status?: string;
}

export interface DubbingPreparation {
  preparation_id: string;
  episode: number;
  group_id: string;
  script_revision: string;
  source_revision: string;
  expected_revision: number;
  source_snapshot_hash: string;
  dialogue_ids: string[];
  source_dialogue_ids: string[];
  dialogues: DubbingDialogue[];
  source_dialogues: DubbingDialogue[];
  missing_voices: string[];
  issues: string[];
}

export interface DubbingTask {
  task_id: string;
  preparation_id: string;
  request_id: string;
  payload_hash: string;
  dialogue_ids: string[];
  status: 'submission_unknown' | 'queued' | 'running' | 'partial_failure' | 'completed' | 'failed';
  backend_task_id: string;
  failed_dialogue_ids: string[];
  error: string;
}

export interface DubbingTake {
  take_id: string;
  preparation_id: string;
  task_id: string;
  dialogue_id: string;
  input_hash: string;
  voice_version_id: string;
  audio_path: string;
  audio_sha256: string;
  actual_duration_seconds: number;
  target_duration_seconds: number | null;
  duration_delta_seconds: number | null;
  provider: string;
  model: string;
  workflow_version: string;
  compiler_version: string;
  effective_parameters: Record<string, unknown>;
  technical_status: 'passed' | 'failed';
  status: 'succeeded' | 'failed';
  error: string;
  url: string;
}

export interface DubbingAdoption {
  take_id: string;
  adopted_by: string;
  reason: string;
  adopted_revision: number;
  created_at: string;
}

export interface DubbingImpact {
  stale_dialogue_ids: string[];
  added_dialogue_ids: string[];
  deleted_dialogue_ids: string[];
  preserved_take_dialogue_ids: string[];
  take_count: number;
  adopted_take_dialogue_ids: string[];
}

export interface DubbingState {
  group_id: string;
  episode: number;
  script_revision: string;
  source_revision: string;
  revision: number;
  dialogues: DubbingDialogue[];
  preparation: DubbingPreparation | null;
  tasks: DubbingTask[];
  takes: DubbingTake[];
  adoption: { revision: number; by_dialogue: Record<string, DubbingAdoption> };
  impact: DubbingImpact;
  issues: string[];
}

export interface DubbingTaskSubmission {
  dubbing_task_id: string;
  task_id: string | null;
  request_id: string;
  status: DubbingTask['status'];
  failed_dialogue_ids: string[];
  error: string;
}

const root = (project: string, episode: number, groupId: string) =>
  `projects/${encodeURIComponent(project)}/episodes/${episode}/narrative-groups/${encodeURIComponent(groupId)}/dubbing`;

export const dubbingApi = {
  get: (project: string, episode: number, groupId: string) =>
    apiCall<DubbingState>(root(project, episode, groupId)),
  prepare: (
    project: string,
    episode: number,
    groupId: string,
    body: {
      script_revision: string;
      dialogue_ids: string[] | null;
      voice_versions: DubbingVoice[];
      performance: Record<string, DubbingPerformanceInput>;
    },
  ) => apiCall<DubbingPreparation>(`${root(project, episode, groupId)}/prepare`, {
    method: 'post',
    retry: 0,
    json: body,
  }),
  generate: (
    project: string,
    episode: number,
    groupId: string,
    preparation_id: string,
    expected_revision: number,
    request_id: string,
  ) => apiCall<DubbingTaskSubmission>(`${root(project, episode, groupId)}/generate`, {
    method: 'post',
    retry: 0,
    json: { preparation_id, expected_revision, request_id },
  }),
  retry: (
    project: string,
    episode: number,
    groupId: string,
    task_id: string,
    failed_dialogue_ids: string[],
    request_id: string,
  ) => apiCall<DubbingTaskSubmission>(`${root(project, episode, groupId)}/retry`, {
    method: 'post',
    retry: 0,
    json: { task_id, failed_dialogue_ids, request_id },
  }),
  adopt: (
    project: string,
    episode: number,
    groupId: string,
    take_ids: string[],
    expected_revision: number,
    reason = '用户试听后采用',
  ) => apiCall<{
    group_id: string;
    revision: number;
    adoption: DubbingState['adoption'];
    impact: DubbingImpact;
  }>(`${root(project, episode, groupId)}/adopt`, {
    method: 'post',
    retry: 0,
    json: { take_ids, expected_revision, reason },
  }),
};
