import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import type { DubbingAdoption, DubbingDialogue, DubbingTake } from './dubbingApi';

interface DubbingAuditionProps {
  sourceKey: string;
  dialogues: DubbingDialogue[];
  takes: DubbingTake[];
  adoptionByDialogue: Record<string, DubbingAdoption>;
}

interface ResolvedTake {
  take?: DubbingTake;
  error?: string;
}

function dialogueLabel(dialogue: DubbingDialogue) {
  return `${dialogue.speaker || dialogue.character_id} · ${dialogue.text}`;
}

export function DubbingAudition({
  sourceKey,
  dialogues,
  takes,
  adoptionByDialogue,
}: DubbingAuditionProps) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const sequenceRef = useRef<string[]>([]);
  const sequenceIndexRef = useRef(0);
  const playbackVersionRef = useRef(0);
  const [focusedDialogueId, setFocusedDialogueId] = useState(dialogues[0]?.dialogue_id ?? '');
  const [selectedTakeIds, setSelectedTakeIds] = useState<Record<string, string>>({});
  const [feedback, setFeedback] = useState('');
  const [feedbackIsError, setFeedbackIsError] = useState(false);
  const [playing, setPlaying] = useState(false);

  const releaseAudio = useCallback((audioToRelease?: HTMLAudioElement | null) => {
    const audio = audioToRelease === undefined ? audioRef.current : audioToRelease;
    if (!audio) return;
    audio.pause();
    audio.currentTime = 0;
    audio.removeAttribute('src');
    audio.load();
  }, []);

  const clearSequence = useCallback((audioToStop?: HTMLAudioElement | null) => {
    playbackVersionRef.current += 1;
    sequenceRef.current = [];
    sequenceIndexRef.current = 0;
    releaseAudio(audioToStop);
  }, [releaseAudio]);

  const stopSequence = useCallback(() => {
    clearSequence();
    setPlaying(false);
  }, [clearSequence]);

  const firstDialogueId = dialogues[0]?.dialogue_id ?? '';
  useEffect(() => {
    const audioForCleanup = audioRef.current;
    stopSequence();
    setFocusedDialogueId(firstDialogueId);
    setSelectedTakeIds({});
    setFeedback('');
    setFeedbackIsError(false);
    return () => clearSequence(audioForCleanup);
  }, [clearSequence, firstDialogueId, sourceKey, stopSequence]);

  const selectedTakeIdFor = useCallback((dialogueId: string) =>
    selectedTakeIds[dialogueId] ?? adoptionByDialogue[dialogueId]?.take_id ?? '',
  [adoptionByDialogue, selectedTakeIds]);

  const resolveTake = useCallback((dialogueId: string): ResolvedTake => {
    const dialogue = dialogues.find(line => line.dialogue_id === dialogueId);
    if (!dialogue) return { error: `对白 ${dialogueId} 不存在，试听已暂停。` };
    if (dialogue.source_status !== 'active') {
      return { error: `${dialogueLabel(dialogue)}（${dialogue.dialogue_id}）源对白状态为 ${dialogue.source_status}，试听已暂停。` };
    }

    const takeId = selectedTakeIdFor(dialogueId);
    if (!takeId) {
      return { error: `${dialogueLabel(dialogue)}（${dialogue.dialogue_id}）缺少已选择的试听 take，试听已暂停。` };
    }
    const take = takes.find(item => item.take_id === takeId && item.dialogue_id === dialogueId);
    if (!take) return { error: `${dialogueLabel(dialogue)}（${dialogue.dialogue_id}）所选 take ${takeId} 缺失，试听已暂停。` };
    if (take.status !== 'succeeded') {
      return { error: `${dialogueLabel(dialogue)}（${dialogue.dialogue_id}）配音失败，试听已暂停。` };
    }
    if (take.technical_status !== 'passed') {
      return { error: `${dialogueLabel(dialogue)}（${dialogue.dialogue_id}）技术检查未通过，试听已暂停。` };
    }
    if (typeof take.url !== 'string' || !take.url.trim()) {
      return { error: `${dialogueLabel(dialogue)}（${dialogue.dialogue_id}）缺少音频地址，试听已暂停。` };
    }
    return { take };
  }, [dialogues, selectedTakeIdFor, takes]);

  const playAt = useCallback(async (index: number, playbackVersion: number) => {
    if (playbackVersion !== playbackVersionRef.current) return;
    const dialogueId = sequenceRef.current[index];
    if (!dialogueId) return;

    const resolved = resolveTake(dialogueId);
    if (!resolved.take) {
      stopSequence();
      setFeedback(resolved.error ?? '试听素材缺失，试听已暂停。');
      setFeedbackIsError(true);
      return;
    }

    const dialogue = dialogues.find(line => line.dialogue_id === dialogueId)!;
    const audio = audioRef.current;
    if (!audio) return;
    audio.pause();
    audio.currentTime = 0;
    audio.src = resolved.take.url;
    audio.load();
    sequenceIndexRef.current = index;
    setPlaying(true);
    setFeedback(`正在试听 ${dialogue.speaker || dialogue.character_id}（${index + 1}/${sequenceRef.current.length}）`);
    setFeedbackIsError(false);
    try {
      await audio.play();
    } catch {
      if (playbackVersion !== playbackVersionRef.current) return;
      stopSequence();
      setFeedback(`${dialogueLabel(dialogue)}（${dialogue.dialogue_id}）无法播放，试听已暂停。`);
      setFeedbackIsError(true);
    }
  }, [dialogues, resolveTake, stopSequence]);

  const handleEnded = useCallback(() => {
    if (!sequenceRef.current.length) return;
    const nextIndex = sequenceIndexRef.current + 1;
    if (nextIndex >= sequenceRef.current.length) {
      stopSequence();
      setFeedback('试听完成。');
      setFeedbackIsError(false);
      return;
    }
    void playAt(nextIndex, playbackVersionRef.current);
  }, [playAt, stopSequence]);

  const handleAudioError = useCallback(() => {
    const dialogueId = sequenceRef.current[sequenceIndexRef.current];
    if (!dialogueId) return;
    const dialogue = dialogues.find(line => line.dialogue_id === dialogueId);
    stopSequence();
    setFeedback(dialogue
      ? `${dialogueLabel(dialogue)}（${dialogue.dialogue_id}）音频加载或解码失败，试听已暂停。`
      : `对白 ${dialogueId} 音频加载或解码失败，试听已暂停。`);
    setFeedbackIsError(true);
  }, [dialogues, stopSequence]);

  const startSequence = useCallback((dialogueIds: string[]) => {
    stopSequence();
    if (!dialogueIds.length) return;
    sequenceRef.current = dialogueIds;
    sequenceIndexRef.current = 0;
    const playbackVersion = playbackVersionRef.current;
    setFeedback('');
    setFeedbackIsError(false);
    void playAt(0, playbackVersion);
  }, [playAt, stopSequence]);

  const focusedIndex = useMemo(() => {
    const index = dialogues.findIndex(line => line.dialogue_id === focusedDialogueId);
    return index < 0 ? 0 : index;
  }, [dialogues, focusedDialogueId]);
  const focusedDialogue = dialogues[focusedIndex];
  const focusedTakeId = focusedDialogue ? selectedTakeIdFor(focusedDialogue.dialogue_id) : '';
  const takesFor = useCallback((dialogueId: string) =>
    takes.filter(take => take.dialogue_id === dialogueId),
  [takes]);

  const selectTake = (dialogueId: string, takeId: string) => {
    stopSequence();
    setFeedback('');
    setFeedbackIsError(false);
    setSelectedTakeIds(current => ({ ...current, [dialogueId]: takeId }));
  };

  const renderTakeOptions = (dialogue: DubbingDialogue, selectedId: string) => {
    const lineTakes = takesFor(dialogue.dialogue_id);
    const adoptedTakeId = adoptionByDialogue[dialogue.dialogue_id]?.take_id;
    const selectedMissing = selectedId && !lineTakes.some(take => take.take_id === selectedId);
    return <>
      <option value="">选择候选或已采用 take</option>
      {selectedMissing && <option value={selectedId}>已选 take 缺失 · {selectedId}</option>}
      {adoptedTakeId && !lineTakes.some(take => take.take_id === adoptedTakeId) && adoptedTakeId !== selectedId &&
        <option value={adoptedTakeId}>已采用 take 缺失 · {adoptedTakeId}</option>}
      {lineTakes.map(take => {
        const isAdopted = adoptedTakeId === take.take_id;
        const inputChanged = Boolean(dialogue.input_hash && take.input_hash && dialogue.input_hash !== take.input_hash);
        const label = [
          isAdopted ? '已采用' : '候选',
          take.status,
          take.technical_status,
          inputChanged ? '输入已变化' : '',
          take.take_id,
        ].filter(Boolean).join(' · ');
        return <option key={take.take_id} value={take.take_id}>{label}</option>;
      })}
    </>;
  };

  const playSingle = () => {
    if (focusedDialogue) startSequence([focusedDialogue.dialogue_id]);
  };
  const playContext = () => {
    if (!dialogues.length) return;
    startSequence(dialogues
      .slice(Math.max(0, focusedIndex - 1), Math.min(dialogues.length, focusedIndex + 2))
      .map(dialogue => dialogue.dialogue_id));
  };
  const playGroup = () => startSequence(dialogues.map(dialogue => dialogue.dialogue_id));

  return <section aria-label="配音试听" className="space-y-3 rounded border p-3">
    <h3 className="font-medium">配音试听</h3>
    {!dialogues.length ? <p className="text-sm text-muted-foreground">当前叙事组没有可试听对白。</p> : <>
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="grid gap-1 text-sm">试听对白<select
          aria-label="试听对白"
          className="rounded border bg-background p-2"
          value={focusedDialogue?.dialogue_id ?? ''}
          onChange={event => {
            stopSequence();
            setFeedback('');
            setFeedbackIsError(false);
            setFocusedDialogueId(event.target.value);
          }}
        >
          {dialogues.map(dialogue => <option key={dialogue.dialogue_id} value={dialogue.dialogue_id}>
            {dialogueLabel(dialogue)}
          </option>)}
        </select></label>
        {focusedDialogue && <label className="grid gap-1 text-sm">试听 take<select
          aria-label={`${focusedDialogue.dialogue_id}试听 take`}
          className="rounded border bg-background p-2"
          value={focusedTakeId}
          onChange={event => selectTake(focusedDialogue.dialogue_id, event.target.value)}
        >{renderTakeOptions(focusedDialogue, focusedTakeId)}</select></label>}
      </div>

      {dialogues.length > 1 && <details className="space-y-2 text-sm">
        <summary className="cursor-pointer text-muted-foreground">为其他对白选择试听 take</summary>
        <div className="space-y-2 pt-2">
          {dialogues.filter(dialogue => dialogue.dialogue_id !== focusedDialogue?.dialogue_id).map(dialogue => {
            const selectedId = selectedTakeIdFor(dialogue.dialogue_id);
            return <label key={dialogue.dialogue_id} className="grid gap-1">
              <span>{dialogueLabel(dialogue)}</span>
              <select
                aria-label={`${dialogue.dialogue_id}试听 take`}
                className="rounded border bg-background p-2"
                value={selectedId}
                onChange={event => selectTake(dialogue.dialogue_id, event.target.value)}
              >{renderTakeOptions(dialogue, selectedId)}</select>
            </label>;
          })}
        </div>
      </details>}

      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={playSingle}>试听单句</Button>
        <Button variant="outline" size="sm" onClick={playContext}>试听前后句</Button>
        <Button variant="outline" size="sm" onClick={playGroup}>试听整组</Button>
        <Button variant="outline" size="sm" disabled={!playing} onClick={() => {
          stopSequence();
          setFeedback('试听已停止。');
          setFeedbackIsError(false);
        }}>停止试听</Button>
      </div>
      {feedback && <p role={feedbackIsError ? 'alert' : 'status'} className={feedbackIsError ? 'text-sm text-destructive' : 'text-sm text-muted-foreground'}>{feedback}</p>}
      <audio ref={audioRef} aria-label="配音试听音频" preload="none" onEnded={handleEnded} onError={handleAudioError} />
    </>}
  </section>;
}
