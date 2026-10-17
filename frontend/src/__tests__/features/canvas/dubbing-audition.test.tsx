import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { DubbingAdoption, DubbingDialogue, DubbingTake } from '@/features/canvas/production/dubbingApi';
import { DubbingAudition } from '@/features/canvas/production/DubbingAudition';

function dialogue(id: string, text: string): DubbingDialogue {
  return {
    dialogue_id: id,
    character_id: `character-${id}`,
    identity_id: '',
    speaker: `角色${id}`,
    script_revision: 'script-r1',
    source_revision: 'source-r1',
    group_id: 'group-1',
    text,
    voice_version_id: 'voice-r1',
    target_duration_seconds: null,
    performance: {},
    source_key: `source-${id}`,
    source_status: 'active',
    input_hash: `hash-${id}`,
  };
}

function take(
  id: string,
  dialogueId: string,
  url: string,
  options: Partial<Pick<DubbingTake, 'status' | 'technical_status' | 'input_hash'>> = {},
): DubbingTake {
  return {
    take_id: id,
    preparation_id: 'prep-1',
    task_id: 'task-1',
    dialogue_id: dialogueId,
    input_hash: options.input_hash ?? `hash-${dialogueId}`,
    voice_version_id: 'voice-r1',
    audio_path: `${id}.wav`,
    audio_sha256: `${id}-sha256`,
    actual_duration_seconds: 1.5,
    target_duration_seconds: null,
    duration_delta_seconds: null,
    provider: 'test',
    model: 'test-model',
    workflow_version: 'workflow-1',
    compiler_version: 'compiler-1',
    effective_parameters: {},
    technical_status: options.technical_status ?? 'passed',
    status: options.status ?? 'succeeded',
    error: '',
    url,
  };
}

function adoption(takeId: string): DubbingAdoption {
  return {
    take_id: takeId,
    adopted_by: 'user-1',
    reason: '试听采用',
    adopted_revision: 1,
    created_at: '2026-10-17T00:00:00Z',
  };
}

const lines = [dialogue('line-a', '第一句。'), dialogue('line-b', '第二句。'), dialogue('line-c', '第三句。')];
const takes = [
  take('take-a', 'line-a', '/line-a.mp3'),
  take('take-b', 'line-b', '/line-b.mp3'),
  take('take-c', 'line-c', '/line-c.mp3'),
];
const adopted = {
  'line-a': adoption('take-a'),
  'line-b': adoption('take-b'),
  'line-c': adoption('take-c'),
};

function renderAudition(props: {
  sourceKey?: string;
  dialogues?: DubbingDialogue[];
  takes?: DubbingTake[];
  adoptionByDialogue?: Record<string, DubbingAdoption>;
} = {}) {
  return render(<DubbingAudition
    sourceKey={props.sourceKey ?? 'project-1:1:group-1:source-r1'}
    dialogues={props.dialogues ?? lines}
    takes={props.takes ?? takes}
    adoptionByDialogue={props.adoptionByDialogue ?? adopted}
  />);
}

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it('plays the selected line and its before/after context in dialogue order', async () => {
  renderAudition();
  const audio = screen.getByLabelText('配音试听音频') as HTMLAudioElement;
  fireEvent.change(screen.getByRole('combobox', { name: '试听对白' }), { target: { value: 'line-b' } });
  fireEvent.click(screen.getByRole('button', { name: '试听前后句' }));

  await waitFor(() => expect(audio.src).toContain('/line-a.mp3'));
  fireEvent.ended(audio);
  await waitFor(() => expect(audio.src).toContain('/line-b.mp3'));
  fireEvent.ended(audio);
  await waitFor(() => expect(audio.src).toContain('/line-c.mp3'));
  fireEvent.ended(audio);
  expect(await screen.findByRole('status')).toHaveTextContent('试听完成');
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(3);
});

it('plays the whole group from the first dialogue in supplied order', async () => {
  renderAudition();
  const audio = screen.getByLabelText('配音试听音频') as HTMLAudioElement;
  fireEvent.change(screen.getByRole('combobox', { name: '试听对白' }), { target: { value: 'line-c' } });
  fireEvent.click(screen.getByRole('button', { name: '试听整组' }));

  await waitFor(() => expect(audio.src).toContain('/line-a.mp3'));
  fireEvent.ended(audio);
  await waitFor(() => expect(audio.src).toContain('/line-b.mp3'));
  fireEvent.ended(audio);
  await waitFor(() => expect(audio.src).toContain('/line-c.mp3'));
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(3);
});

it('plays an explicitly selected candidate without adopting it', async () => {
  renderAudition({ takes: [...takes, take('candidate-b', 'line-b', '/candidate-b.mp3')] });
  fireEvent.change(screen.getByRole('combobox', { name: '试听对白' }), { target: { value: 'line-b' } });
  fireEvent.change(screen.getByRole('combobox', { name: 'line-b试听 take' }), { target: { value: 'candidate-b' } });
  fireEvent.click(screen.getByRole('button', { name: '试听单句' }));

  await waitFor(() => expect((screen.getByLabelText('配音试听音频') as HTMLAudioElement).src).toContain('/candidate-b.mp3'));
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1);
});

it('pauses at a failed dialogue instead of skipping to the next take', async () => {
  const failedTake = take('take-b-failed', 'line-b', '/line-b-failed.mp3', { status: 'failed', technical_status: 'failed' });
  renderAudition({
    takes: [takes[0], failedTake, takes[2]],
    adoptionByDialogue: {
      'line-a': adopted['line-a'],
      'line-b': adoption('take-b-failed'),
      'line-c': adopted['line-c'],
    },
  });
  const audio = screen.getByLabelText('配音试听音频') as HTMLAudioElement;
  fireEvent.click(screen.getByRole('button', { name: '试听整组' }));
  await waitFor(() => expect(audio.src).toContain('/line-a.mp3'));
  fireEvent.ended(audio);

  expect(await screen.findByRole('alert')).toHaveTextContent(/line-b.*配音失败.*试听已暂停/);
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1);
});

it('refuses a take without a URL and reports the missing audio', async () => {
  renderAudition({ takes: [take('take-b-empty', 'line-b', '')], adoptionByDialogue: { 'line-b': adoption('take-b-empty') } });
  fireEvent.change(screen.getByRole('combobox', { name: '试听对白' }), { target: { value: 'line-b' } });
  fireEvent.click(screen.getByRole('button', { name: '试听单句' }));

  expect(await screen.findByRole('alert')).toHaveTextContent(/line-b.*缺少音频地址/);
  expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled();
});

it('stops playback when the source changes and when the component unmounts', async () => {
  const view = renderAudition();
  const audio = screen.getByLabelText('配音试听音频') as HTMLAudioElement;
  fireEvent.click(screen.getByRole('button', { name: '试听单句' }));
  await waitFor(() => expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1));

  view.rerender(<DubbingAudition sourceKey="project-1:1:group-1:source-r2" dialogues={lines} takes={takes} adoptionByDialogue={adopted} />);
  await waitFor(() => expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled());
  expect(audio.hasAttribute('src')).toBe(false);

  fireEvent.click(screen.getByRole('button', { name: '试听单句' }));
  await waitFor(() => expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2));
  vi.mocked(HTMLMediaElement.prototype.pause).mockClear();
  view.unmount();
  expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(1);
});

it('stops playback when the dialogue list becomes empty', async () => {
  const view = renderAudition();
  fireEvent.click(screen.getByRole('button', { name: '试听单句' }));
  await waitFor(() => expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1));
  vi.mocked(HTMLMediaElement.prototype.pause).mockClear();

  view.rerender(<DubbingAudition sourceKey="project-1:1:group-1:source-r1" dialogues={[]} takes={takes} adoptionByDialogue={adopted} />);

  await waitFor(() => expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(1));
});

it('reports media load or decode errors and does not continue the sequence', async () => {
  renderAudition();
  const audio = screen.getByLabelText('配音试听音频') as HTMLAudioElement;
  fireEvent.click(screen.getByRole('button', { name: '试听整组' }));
  await waitFor(() => expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1));

  fireEvent.error(audio);

  expect(await screen.findByRole('alert')).toHaveTextContent(/line-a.*加载或解码失败.*试听已暂停/);
  fireEvent.ended(audio);
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1);
});
