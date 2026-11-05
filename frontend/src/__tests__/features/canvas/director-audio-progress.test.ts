import { expect, it } from 'vitest';
import { collectDirectorAudioOutputs } from '@/features/canvas/production/directorAudioProgress';

const baseGroup = (video: Record<string, unknown>) => ({
  id: 'group-a', director_revision_id: 'revision-1',
  stages: { video: { status: 'completed', ...video } },
});

it('requires external dialogue and separated ambience files instead of treating original audio as dubbed dialogue', () => {
  const result = collectDirectorAudioOutputs('project', ['group-a'], 'revision-1', [baseGroup({
    original_audio_path: '/api/v1/projects/project/media/episodes/003/original.mp3',
    ambience_stem_path: '/api/v1/projects/project/media/episodes/003/ambience.mp3',
    ambience_stem_status: 'succeeded',
    video_spans: [{ has_dialogue: true, dialogue_source: 'external_tts', external_audio_ready: false }],
  })]);

  expect(result.ready).toBe(false);
  expect(result.missing).toContain('group-a：对白外部配音文件');
  expect(result.audio).toEqual([]);
  expect(result.audio).not.toContain('episodes/003/original.mp3');
});

it('registers only a real original mix after an explicit native-dialogue choice', () => {
  const result = collectDirectorAudioOutputs('project', ['group-a'], 'revision-1', [baseGroup({
    original_audio_path: '/api/v1/projects/project/media/episodes/003/native.mp3',
    video_spans: [{ has_dialogue: true, dialogue_source: 'h3_native' }],
  })]);

  expect(result.ready).toBe(true);
  expect(result.audio).toEqual(['episodes/003/native.mp3']);
});

it('does not require dubbing for dialogue-free spans and registers available ambience as audio', () => {
  const result = collectDirectorAudioOutputs('project', ['group-a'], 'revision-1', [baseGroup({
    ambience_stem_path: '/api/v1/projects/project/media/episodes/003/ambience.mp3',
    ambience_stem_status: 'succeeded',
    video_spans: [{ has_dialogue: false, dialogue_source: 'external_tts', external_audio_ready: false }],
  })]);

  expect(result.ready).toBe(true);
  expect(result.audio).toEqual(['episodes/003/ambience.mp3']);
  expect(result.ambience).toEqual(['episodes/003/ambience.mp3']);
});

it('keeps the audio receipt incomplete when the backend omitted the dialogue-presence fact', () => {
  const result = collectDirectorAudioOutputs('project', ['group-a'], 'revision-1', [baseGroup({
    original_audio_path: '/api/v1/projects/project/media/episodes/003/original.mp3',
    video_spans: [{ dialogue_source: 'h3_native' }],
  })]);

  expect(result.ready).toBe(false);
  expect(result.missing).toContain('group-a：对白状态缺失');
});

it('collects all external dialogue paths and separated ambience only when every spoken span is ready', () => {
  const result = collectDirectorAudioOutputs('project', ['group-a'], 'revision-1', [baseGroup({
    ambience_stem_path: '/api/v1/projects/project/media/episodes/003/ambience.mp3',
    ambience_stem_status: 'succeeded',
    video_spans: [
      { has_dialogue: true, dialogue_source: 'external_tts', external_audio_ready: true, external_audio_path: '/api/v1/projects/project/media/episodes/003/dialogue-1.mp3' },
      { has_dialogue: true, dialogue_source: 'external_tts', external_audio_ready: true, external_audio_path: '/api/v1/projects/project/media/episodes/003/dialogue-2.mp3' },
    ],
  })]);

  expect(result.ready).toBe(true);
  expect(result.audio).toEqual(['episodes/003/dialogue-1.mp3', 'episodes/003/dialogue-2.mp3']);
  expect(result.ambience).toEqual(['episodes/003/ambience.mp3']);
});

it('does not combine a whole-group native mix with external span dubbing', () => {
  const result = collectDirectorAudioOutputs('project', ['group-a'], 'revision-1', [baseGroup({
    original_audio_path: '/api/v1/projects/project/media/episodes/003/original.mp3',
    ambience_stem_path: '/api/v1/projects/project/media/episodes/003/ambience.mp3',
    ambience_stem_status: 'succeeded',
    video_spans: [
      { has_dialogue: true, dialogue_source: 'h3_native' },
      { has_dialogue: true, dialogue_source: 'external_tts', external_audio_ready: true, external_audio_path: '/api/v1/projects/project/media/episodes/003/dialogue.mp3' },
    ],
  })]);

  expect(result.ready).toBe(false);
  expect(result.missing).toContain('group-a：同组对白来源需统一为独立配音或 H3 原声');
  expect(result.audio).toEqual([]);
});
