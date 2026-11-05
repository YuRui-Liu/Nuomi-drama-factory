import { expect, it, vi } from 'vitest';
import { apiCall } from '@/api/client';
import { changeDirectorGroupDialogueSource } from '@/features/canvas/production/directorProductionAudioApi';

vi.mock('@/api/client', () => ({ apiCall: vi.fn() }));

it('submits an explicit per-span source change through the existing narrative-group action', async () => {
  vi.mocked(apiCall).mockResolvedValue({ ok: true, data: { task_id: 'task-1' } });

  await changeDirectorGroupDialogueSource('demo project', 3, 'group/a', 2, 'external_tts', 7);

  expect(apiCall).toHaveBeenCalledWith(
    'projects/demo%20project/episodes/3/narrative-groups/group%2Fa/video/dialogue-source',
    { method: 'post', json: { span_index: 2, dialogue_source: 'external_tts', revision: 7 } },
  );
});
