import { apiCall } from '@/api/client';

export type ProductionStageName = 'screenplay' | 'assets' | 'storyboard' | 'video' | 'audio' | 'composition';
export type ProductionStageStatus = 'pending' | 'running' | 'awaiting_review' | 'ready' | 'failed';

export interface ProductionStageProgress {
  status: ProductionStageStatus;
  input_hash: string | null;
  outputs: Record<string, unknown>;
  output_hash: string | null;
  approval_hash: string | null;
  approved_hash: string | null;
  review_resolution: string | null;
  error: string | null;
}

export interface ProductionProgress {
  episode_id: string;
  mode: 'review' | 'auto';
  stop_after: ProductionStageName | null;
  stages: Record<ProductionStageName, ProductionStageProgress>;
  blocked: { stage: ProductionStageName; episode_id: string; reason: string } | null;
}

const progressPath = (project: string, canvas: string, episode: string) =>
  `projects/${encodeURIComponent(project)}/freezone/orchestration/canvases/${encodeURIComponent(canvas)}/progress/${encodeURIComponent(episode)}`;

export const productionProgressApi = {
  get: (project: string, canvas: string, episode: string) =>
    apiCall<ProductionProgress>(progressPath(project, canvas, episode)),
  configure: (project: string, canvas: string, episode: string, body: {
    mode?: 'review' | 'auto';
    stop_after?: ProductionStageName | null;
  }) => apiCall<ProductionProgress>(`${progressPath(project, canvas, episode)}/configure`, {
    method: 'post', retry: 0, json: body,
  }),
  complete: (project: string, canvas: string, episode: string, body: {
    stage: ProductionStageName;
    input_hash: string;
    outputs: Record<string, unknown>;
  }) => apiCall<ProductionProgress>(`${progressPath(project, canvas, episode)}/complete`, {
    method: 'post', retry: 0, json: body,
  }),
  approve: (project: string, canvas: string, episode: string, body: {
    stage: ProductionStageName;
    input_hash: string;
  }) => apiCall<ProductionProgress>(`${progressPath(project, canvas, episode)}/approve`, {
    method: 'post', retry: 0, json: body,
  }),
};
