import { apiCall } from '@/api/client';

export type NarrativeDialogueSource = 'external_tts' | 'h3_native';

export interface DialogueSourceChangeResult {
  ok: boolean;
  data: {
    task_id: string | null;
    recomposition_deferred?: boolean;
  };
}

export function changeDirectorGroupDialogueSource(
  project: string,
  episode: number,
  groupId: string,
  spanIndex: number,
  dialogueSource: NarrativeDialogueSource,
  revision: number,
): Promise<DialogueSourceChangeResult> {
  const path = `projects/${encodeURIComponent(project)}/episodes/${episode}/narrative-groups/${encodeURIComponent(groupId)}/video/dialogue-source`;
  return apiCall<DialogueSourceChangeResult>(path, {
    method: 'post',
    json: { span_index: spanIndex, dialogue_source: dialogueSource, revision },
  });
}
