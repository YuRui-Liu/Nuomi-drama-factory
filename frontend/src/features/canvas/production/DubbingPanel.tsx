import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { useCanvasStore } from '@/stores/canvasStore';
import { awaitTaskCompletion } from '@/api/tasks';
import {
  fetchFreezoneJobResult,
  submitFreezoneAudioSpeech,
  type FreezoneAudioVoiceRef,
} from '@/api/ops';
import { readUrl } from '@/lib/url-params';
import { CANVAS_NODE_TYPES } from '../domain/canvasNodes';
import {
  dubbingApi,
  type DubbingDialogue,
  type DubbingPerformanceInput,
  type DubbingPerformanceMode,
  type DubbingTask,
  type DubbingVoice,
} from './dubbingApi';
import { useProductionAction } from './useProductionAction';
import { directorWorkspaceApi } from './directorWorkspaceApi';
import { DubbingAudition } from './DubbingAudition';

interface PerformanceDraft {
  mode: DubbingPerformanceMode;
  direction: string;
  payload: string;
}

interface AuditionDraft {
  text: string;
  emotionPrompt: string;
  voiceVersionId: string;
}

interface AuditionVoice {
  character_id: string;
  character_name: string;
  identity_id: string;
  slot: string;
  version_id: string;
}

function draftKey(episode: number, groupId: string, dialogueId: string) {
  return `${episode}:${groupId}:${dialogueId}`;
}

function draftFromDialogue(line: DubbingDialogue): PerformanceDraft {
  const performance = line.performance ?? {};
  const effective = typeof performance.effective === 'object' && performance.effective !== null
    ? performance.effective as Record<string, unknown>
    : {};
  const reviewOnly = typeof performance.review_only === 'object' && performance.review_only !== null
    ? performance.review_only as Record<string, unknown>
    : {};
  const mode = effective.mode === 'text' || effective.emotion_text
    ? 'emotion_text'
    : 'neutral';
  return {
    mode,
    direction: String(reviewOnly.director_note ?? performance.direction ?? ''),
    payload: String(effective.emotion_text ?? performance.payload ?? ''),
  };
}

function readPerformanceDrafts(value: unknown): Record<string, PerformanceDraft> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return {};
  const drafts: Record<string, PerformanceDraft> = {};
  for (const [key, item] of Object.entries(value)) {
    if (typeof item !== 'object' || item === null || Array.isArray(item)) continue;
    const draft = item as Partial<PerformanceDraft>;
    drafts[key] = {
      mode: draft.mode === 'emotion_text' ? 'emotion_text' : 'neutral',
      direction: typeof draft.direction === 'string' ? draft.direction : '',
      payload: typeof draft.payload === 'string' ? draft.payload : '',
    };
  }
  return drafts;
}

function normalizeDraft(draft: PerformanceDraft): DubbingPerformanceInput {
  const payload = draft.payload.trim();
  return {
    mode: draft.mode,
    direction: draft.direction,
    ...(draft.mode === 'emotion_text' && payload ? { payload } : {}),
  };
}

function readAuditionDraft(value: unknown): AuditionDraft {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return { text: '', emotionPrompt: '', voiceVersionId: '' };
  }
  const draft = value as Partial<AuditionDraft>;
  return {
    text: typeof draft.text === 'string' ? draft.text : '',
    emotionPrompt: typeof draft.emotionPrompt === 'string' ? draft.emotionPrompt : '',
    voiceVersionId: typeof draft.voiceVersionId === 'string' ? draft.voiceVersionId : '',
  };
}

function sortedVoices(voices: DubbingVoice[]) {
  return [...voices].sort((left, right) =>
    `${left.character_id}\u0000${left.identity_id}\u0000${left.version_id}`
      .localeCompare(`${right.character_id}\u0000${right.identity_id}\u0000${right.version_id}`));
}

function performanceStatus(task: DubbingTask) {
  if (task.status === 'submission_unknown') return '提交状态未知，正在查询；系统不会自动重复提交。';
  if (task.status === 'partial_failure') return `部分失败 · ${task.failed_dialogue_ids.length} 行待重试`;
  if (task.status === 'failed') return `失败 · ${task.failed_dialogue_ids.length} 行待重试`;
  if (task.status === 'queued') return '排队中';
  if (task.status === 'running') return '生成中';
  if (task.status === 'completed') return '已完成';
  return task.status;
}

export function DubbingPanel({ nodeId }: { nodeId: string }) {
  const project = readUrl().project ?? '';
  const nodes = useCanvasStore(state => state.nodes);
  const edges = useCanvasStore(state => state.edges);
  const update = useCanvasStore(state => state.updateNodeData);
  const data = nodes.find(node => node.id === nodeId)?.data;
  const episode = Number(data?.episodeNumber ?? 1);
  const group = typeof data?.groupId === 'string' ? data.groupId : '';
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [performanceDrafts, setPerformanceDrafts] = useState<Record<string, PerformanceDraft>>(() =>
    readPerformanceDrafts(data?.dubbingPerformanceDrafts));
  const { busy, error, perform } = useProductionAction();

  const workspace = useQuery({
    queryKey: ['director-workspace', project, episode],
    queryFn: () => directorWorkspaceApi.get(project, episode),
    enabled: Boolean(open && project),
  });
  const state = useQuery({
    queryKey: ['group-dubbing', project, episode, group],
    queryFn: () => dubbingApi.get(project, episode, group),
    enabled: Boolean(open && project && group),
    refetchInterval: query => query.state.data?.tasks.some(task =>
      ['submission_unknown', 'queued', 'running'].includes(task.status)) ? 3000 : false,
  });

  const scriptRevision = state.data?.script_revision || workspace.data?.script?.revision_id || '';
  const sourceRevision = state.data?.source_revision ?? '';
  const prep = state.data?.preparation;
  const dialogues = state.data?.dialogues.length
    ? state.data.dialogues
    : prep?.source_dialogues.length
      ? prep.source_dialogues
      : prep?.dialogues ?? [];

  const upstream = new Set(edges.filter(edge => edge.target === nodeId).map(edge => edge.source));
  const voiceBindings = new Map<string, DubbingVoice>();
  for (const node of nodes) {
    if (!upstream.has(node.id) || node.type !== CANVAS_NODE_TYPES.characterVoice) continue;
    const characterId = typeof node.data.characterId === 'string' ? node.data.characterId : '';
    const versionId = typeof node.data.voiceVersionId === 'string' ? node.data.voiceVersionId : '';
    if (!characterId || !versionId) continue;
    const identityId = typeof node.data.identityId === 'string' ? node.data.identityId : '';
    voiceBindings.set(`${characterId}\u0000${identityId}`, {
      character_id: characterId,
      identity_id: identityId,
      version_id: versionId,
    });
  }
  const voices = sortedVoices([...voiceBindings.values()]);
  const auditionVoiceMap = new Map<string, AuditionVoice>();
  for (const source of nodes) {
    if (!upstream.has(source.id) || source.type !== CANVAS_NODE_TYPES.characterVoice) continue;
    const reference = typeof source.data.voiceReference === 'object' && source.data.voiceReference !== null
      ? source.data.voiceReference as Record<string, unknown>
      : {};
    const characterId = typeof source.data.characterId === 'string'
      ? source.data.characterId
      : typeof reference.character_id === 'string' ? reference.character_id : '';
    const versionId = typeof source.data.voiceVersionId === 'string'
      ? source.data.voiceVersionId
      : typeof reference.version_id === 'string' ? reference.version_id : '';
    if (!characterId || !versionId) continue;
    const identityId = typeof source.data.identityId === 'string'
      ? source.data.identityId
      : typeof reference.identity_id === 'string' ? reference.identity_id : '';
    const characterName = typeof source.data.characterName === 'string'
      ? source.data.characterName
      : typeof reference.character_name === 'string' ? reference.character_name : characterId;
    const slot = typeof source.data.slot === 'string'
      ? source.data.slot
      : typeof reference.slot === 'string' ? reference.slot : 'default';
    const voice: AuditionVoice = {
      character_id: characterId,
      character_name: characterName,
      identity_id: identityId,
      slot: slot || 'default',
      version_id: versionId,
    };
    auditionVoiceMap.set(`${characterId}\u0000${identityId}\u0000${voice.slot}\u0000${versionId}`, voice);
  }
  const auditionVoices = [...auditionVoiceMap.values()].sort((left, right) =>
    `${left.character_name}\u0000${left.identity_id}\u0000${left.version_id}`
      .localeCompare(`${right.character_name}\u0000${right.identity_id}\u0000${right.version_id}`));
  const auditionDraft = readAuditionDraft(data?.auditionDraft);
  const selectedAuditionVoice = auditionVoices.find(item => item.version_id === auditionDraft.voiceVersionId)
    ?? auditionVoices[0];
  const auditionResult = typeof data?.auditionResult === 'object' && data.auditionResult !== null
    ? data.auditionResult as Record<string, unknown>
    : null;
  const auditionResultUrl = typeof auditionResult?.url === 'string' ? auditionResult.url : '';

  useEffect(() => {
    setPerformanceDrafts(current => {
      let changed = false;
      const next = { ...current };
      for (const line of dialogues) {
        const key = draftKey(episode, group, line.dialogue_id);
        if (next[key]) continue;
        next[key] = draftFromDialogue(line);
        changed = true;
      }
      return changed ? next : current;
    });
  }, [dialogues, episode, group]);

  useEffect(() => {
    if (!dialogues.length) return;
    const ids = dialogues.map(line => line.dialogue_id);
    const saved = Array.isArray(data?.dialogueIds)
      ? data.dialogueIds.filter((value): value is string => typeof value === 'string')
      : [];
    if (!saved.length || saved.some(id => !ids.includes(id))) {
      update(nodeId, { dialogueIds: ids });
    }
  }, [data?.dialogueIds, dialogues, nodeId, update]);

  const performanceFor = (line: DubbingDialogue) =>
    readPerformanceDrafts(data?.dubbingPerformanceDrafts)[draftKey(episode, group, line.dialogue_id)]
      ?? performanceDrafts[draftKey(episode, group, line.dialogue_id)]
      ?? draftFromDialogue(line);
  const performanceInput = Object.fromEntries(dialogues
    .map(line => [line.dialogue_id, normalizeDraft(performanceFor(line))] as const)
    .sort(([left], [right]) => left.localeCompare(right)));
  const currentInputFingerprint = JSON.stringify({
    episode,
    group,
    script_revision: scriptRevision,
    source_revision: sourceRevision,
    dialogue_ids: dialogues.map(line => line.dialogue_id).sort(),
    voice_versions: voices,
    performance: performanceInput,
  });
  const emotionDraftsMissingText = dialogues.filter(line => {
    const draft = performanceFor(line);
    return draft.mode === 'emotion_text' && !draft.payload.trim();
  });
  const preparedInputFingerprint = typeof data?.preparedInputFingerprint === 'string'
    ? data.preparedInputFingerprint
    : '';
  const preparedPreparationId = typeof data?.preparedDubbingPreparationId === 'string'
    ? data.preparedDubbingPreparationId
    : '';
  const preparationMatchesCurrent = Boolean(
    prep
    && state.data
    && preparedPreparationId === prep.preparation_id
    && preparedInputFingerprint === currentInputFingerprint
    && prep.episode === episode
    && prep.group_id === group
    && prep.script_revision === scriptRevision
    && prep.source_revision === sourceRevision
    && prep.expected_revision === state.data.revision
    && prep.dialogue_ids.length === dialogues.length
    && prep.dialogue_ids.every(id => dialogues.some(line => line.dialogue_id === id)),
  );
  const pendingTaskForPreparation = Boolean(prep && state.data?.tasks.some(task =>
    task.preparation_id === prep.preparation_id
    && ['submission_unknown', 'queued', 'running'].includes(task.status)));
  const cannotPrepare = busy || !project || !group || !scriptRevision || !sourceRevision
    || !dialogues.length || emotionDraftsMissingText.length > 0;
  const adoptedTakes = new Set(Object.values(state.data?.adoption.by_dialogue ?? {}).map(item => item.take_id));

  const updatePerformance = (line: DubbingDialogue, patch: Partial<PerformanceDraft>) => {
    const key = draftKey(episode, group, line.dialogue_id);
    const liveData = useCanvasStore.getState().nodes.find(node => node.id === nodeId)?.data;
    const storedDrafts = readPerformanceDrafts(liveData?.dubbingPerformanceDrafts);
    const nextDraft = { ...(storedDrafts[key] ?? performanceDrafts[key] ?? draftFromDialogue(line)), ...patch };
    setPerformanceDrafts(current => ({ ...current, [key]: nextDraft }));
    const nextPerformance = Object.fromEntries(dialogues
      .map(item => [
        item.dialogue_id,
        normalizeDraft(item.dialogue_id === line.dialogue_id ? nextDraft : performanceFor(item)),
      ] as const)
      .sort(([left], [right]) => left.localeCompare(right)));
    update(nodeId, {
      dubbingPerformanceDrafts: {
        ...storedDrafts,
        [key]: nextDraft,
      },
      performance: nextPerformance,
    });
  };

  const updateAuditionDraft = (patch: Partial<AuditionDraft>) => {
    const liveData = useCanvasStore.getState().nodes.find(node => node.id === nodeId)?.data;
    const next = { ...readAuditionDraft(liveData?.auditionDraft), ...patch };
    update(nodeId, { operation: 'audition', auditionDraft: next });
  };

  const runFreeAudition = () => {
    if (!selectedAuditionVoice || !auditionDraft.text.trim()) return;
    const voiceRef: FreezoneAudioVoiceRef = {
      scope: 'character_voice_version',
      characterName: selectedAuditionVoice.character_name,
      characterId: selectedAuditionVoice.character_id,
      identityId: selectedAuditionVoice.identity_id,
      slot: selectedAuditionVoice.slot,
      versionId: selectedAuditionVoice.version_id,
    };
    const frozenDraft = {
      ...auditionDraft,
      voiceVersionId: selectedAuditionVoice.version_id,
    };
    void perform(`audition:${selectedAuditionVoice.version_id}:${frozenDraft.text}:${frozenDraft.emotionPrompt}`, async () => {
      if (!project) throw new Error('请先选择项目');
      update(nodeId, { operation: 'audition', auditionDraft: frozenDraft });
      const task = await submitFreezoneAudioSpeech(project, {
        text: frozenDraft.text,
        emotionPrompt: frozenDraft.emotionPrompt,
        voiceRef,
      });
      if (!task.task_key || !task.job_id) throw new Error('自由试音任务没有返回可追踪标识');
      await awaitTaskCompletion(task.task_key, project);
      const result = await fetchFreezoneJobResult(project, 'freezone_audio_speech', task.job_id);
      if (!result.url) throw new Error('自由试音完成但没有音频结果');
      update(nodeId, {
        auditionResult: {
          url: result.url,
          jobId: task.job_id,
          voiceVersionId: frozenDraft.voiceVersionId,
          text: frozenDraft.text,
        },
      });
    });
  };

  const retryTask = (task: DubbingTask) => {
    if (!task.failed_dialogue_ids.length) return;
    const failedIds = [...new Set(task.failed_dialogue_ids)].sort();
    void perform(`retry:${task.task_id}:${failedIds.join(',')}`, async mutation => {
      await dubbingApi.retry(project, episode, group, task.task_id, failedIds, mutation);
      await state.refetch();
    });
  };

  return <Dialog open={open} onOpenChange={setOpen}>
    <Button variant="outline" size="sm" onClick={() => setOpen(true)}>剧本配音与表演调整</Button>
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
      <DialogHeader><DialogTitle>叙事组配音 · IndexTTS2</DialogTitle></DialogHeader>
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <label>集数<input aria-label="配音集数" type="number" min={1} className="w-full rounded border bg-background p-2" value={episode} onChange={event => {
            setSelected([]);
            update(nodeId, { episodeNumber: Number(event.target.value) });
          }} /></label>
          <label className="sm:col-span-2">叙事组<select aria-label="配音叙事组" className="w-full rounded border bg-background p-2" value={group} onChange={event => {
            setSelected([]);
            update(nodeId, { groupId: event.target.value });
          }}>
            <option value="">选择已规划的叙事组</option>
            {workspace.data?.groups?.map(item => <option key={String(item.id)} value={String(item.id)}>
              {String(item.objective || item.scene_anchor || item.id)}
            </option>)}
          </select></label>
        </div>
        {!scriptRevision && <p className="text-sm text-muted-foreground">请先采用本集剧本并交给导演，配音会引用该版对白。</p>}
        <p className="text-sm text-muted-foreground">
          已连接 {voices.length} 个固定声音版本。对白按叙事组准备；每行表演草稿需重新准备后才能提交生成。候选不会自动采用或覆盖剪辑素材。
        </p>
        {(error || state.error || workspace.error) && <p role="alert" className="text-destructive">{error || String(state.error || workspace.error)}</p>}
        {state.data?.issues.map(issue => <p key={issue} role="alert" className="text-sm text-destructive">{issue}</p>)}
        {emotionDraftsMissingText.length > 0 && <p role="alert" className="text-sm text-destructive">
          文本情绪模式需要填写情绪描述：{emotionDraftsMissingText.map(line => line.dialogue_id).join('、')}
        </p>}

        <section aria-label="自由试音" className="space-y-3 rounded border p-3">
          <div>
            <h3 className="font-medium">自由试音 · 固定声音版本</h3>
            <p className="text-sm text-muted-foreground">临时文本只用于试听，不会改写正式对白、配音候选或时间线。</p>
          </div>
          <label className="grid gap-1 text-sm">声音版本<select
            aria-label="自由试音声音版本"
            className="rounded border bg-background p-2"
            value={selectedAuditionVoice?.version_id ?? ''}
            onChange={event => updateAuditionDraft({ voiceVersionId: event.target.value })}
            disabled={!auditionVoices.length}
          >
            {auditionVoices.map(voice => <option key={`${voice.character_id}:${voice.identity_id}:${voice.slot}:${voice.version_id}`} value={voice.version_id}>
              {voice.character_name}{voice.identity_id ? ` · ${voice.identity_id}` : ''} · {voice.version_id}
            </option>)}
          </select></label>
          <label className="grid gap-1 text-sm">临时试音文本<textarea
            aria-label="自由试音文本"
            className="min-h-20 rounded border bg-background p-2"
            maxLength={10_000}
            value={auditionDraft.text}
            onChange={event => updateAuditionDraft({ text: event.target.value })}
            placeholder="输入临时文本；不会写回已采用剧本"
          /></label>
          <label className="grid gap-1 text-sm">情绪描述<textarea
            aria-label="自由试音情绪描述"
            className="min-h-12 rounded border bg-background p-2"
            maxLength={2_000}
            value={auditionDraft.emotionPrompt}
            onChange={event => updateAuditionDraft({ emotionPrompt: event.target.value })}
            placeholder="可选；例如：克制地压低音量"
          /></label>
          <Button
            variant="outline"
            disabled={busy || !project || !selectedAuditionVoice || !auditionDraft.text.trim()}
            onClick={runFreeAudition}
          >自由试音</Button>
          {auditionResultUrl && <div className="space-y-1">
            <p className="text-sm text-muted-foreground">试听结果 · {String(auditionResult?.voiceVersionId ?? '')}</p>
            <audio aria-label="自由试音试听结果" controls src={auditionResultUrl} className="w-full" />
          </div>}
        </section>

        {dialogues.map(line => {
          const draft = performanceFor(line);
          const adoptedTakeId = state.data?.adoption.by_dialogue[line.dialogue_id]?.take_id;
          const lineTakes = state.data?.takes.filter(take => take.dialogue_id === line.dialogue_id) ?? [];
          return <article key={line.dialogue_id} className="space-y-3 rounded border p-3">
            <div>
              <p className="text-sm text-muted-foreground">{line.speaker || line.character_id} · {line.identity_id || '默认身份'}</p>
              <p>{line.text}</p>
              {line.target_duration_seconds !== null && <p className="text-xs text-muted-foreground">目标时长 {line.target_duration_seconds.toFixed(2)} 秒</p>}
              {line.source_status !== 'active' && <p className="text-xs text-destructive">源对白状态：{line.source_status}</p>}
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <label className="grid gap-1 text-sm">声音情绪模式<select
                aria-label={`${line.dialogue_id}声音情绪模式`}
                className="rounded border bg-background p-2"
                value={draft.mode}
                onChange={event => updatePerformance(line, { mode: event.target.value as DubbingPerformanceMode })}
              >
                <option value="neutral">中性</option>
                <option value="emotion_text">文本情绪</option>
              </select></label>
              <label className="grid gap-1 text-sm">表演指导<textarea
                aria-label={`${line.dialogue_id}表演指导`}
                className="min-h-10 rounded border bg-background p-2"
                value={draft.direction}
                onChange={event => updatePerformance(line, { direction: event.target.value })}
                placeholder="人物意图、对象、潜台词、动作节拍；供审阅，不改写台词"
              /></label>
              <label className="grid gap-1 text-sm sm:col-span-2">文本情绪描述<textarea
                aria-label={`${line.dialogue_id}文本情绪描述`}
                className="min-h-10 rounded border bg-background p-2"
                value={draft.payload}
                disabled={draft.mode !== 'emotion_text'}
                onChange={event => updatePerformance(line, { payload: event.target.value })}
                placeholder="例如：克制、压低音量，愤怒逐渐显露"
              /></label>
            </div>
            {lineTakes.map(take => {
              const checked = selected.includes(take.take_id);
              return <div key={take.take_id} className="space-y-2 border-t pt-2">
                <label className="flex flex-wrap items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={take.status !== 'succeeded' || take.technical_status !== 'passed'}
                    onChange={event => setSelected(current => event.target.checked
                      ? [...current.filter(id => !lineTakes.some(item => item.take_id === id)), take.take_id]
                      : current.filter(id => id !== take.take_id))}
                  />
                  {take.status} · {take.actual_duration_seconds.toFixed(2)} 秒
                  {take.target_duration_seconds !== null && ` · 目标 ${take.target_duration_seconds.toFixed(2)} 秒`}
                  {take.duration_delta_seconds !== null && ` · 偏差 ${take.duration_delta_seconds.toFixed(2)} 秒`}
                  {adoptedTakeId === take.take_id && <span className="font-medium">· 已采用</span>}
                  {take.error && <span className="text-destructive">· {take.error}</span>}
                </label>
              </div>;
            })}
          </article>;
        })}

        <DubbingAudition
          sourceKey={`${project}:${episode}:${group}:${scriptRevision}:${sourceRevision}`}
          dialogues={dialogues}
          takes={state.data?.takes ?? []}
          adoptionByDialogue={state.data?.adoption.by_dialogue ?? {}}
        />

        <div className="flex flex-wrap gap-2">
          <Button disabled={cannotPrepare} onClick={() => void perform(`prepare:${group}:${currentInputFingerprint}`, async () => {
            const result = await dubbingApi.prepare(project, episode, group, {
              script_revision: scriptRevision,
              dialogue_ids: null,
              voice_versions: voices,
              performance: performanceInput,
            });
            update(nodeId, {
              operation: 'group_dubbing',
              episodeNumber: episode,
              groupId: group,
              scriptRevision,
              sourceRevision,
              dialogueIds: dialogues.map(line => line.dialogue_id),
              performance: performanceInput,
              preparedInputFingerprint: currentInputFingerprint,
              preparedDubbingPreparationId: result.preparation_id,
            });
            await state.refetch();
          })}>{prep ? '更新对白与表演方案' : '准备对白与声音引用'}</Button>
          <Button disabled={busy || !prep || !preparationMatchesCurrent || pendingTaskForPreparation || Boolean(prep.missing_voices.length || prep.issues.length)} onClick={() => void perform(`generate:${prep?.preparation_id}`, async mutation => {
            await dubbingApi.generate(project, episode, group, prep!.preparation_id, prep!.expected_revision, mutation);
            await state.refetch();
          })}>生成配音候选</Button>
          <Button variant="outline" onClick={() => void state.refetch()}>查询任务与候选</Button>
        </div>
        {prep && !preparationMatchesCurrent && <p role="status" className="text-sm text-amber-700">
          当前对白、剧本版本、声音引用或逐行表演草稿与准备快照不一致。请重新准备后再生成。
        </p>}
        {prep?.missing_voices.length ? <div role="alert" className="text-sm text-destructive">
          缺少已采用声音版本：{prep.missing_voices.join('、')}。请连接对应角色的固定声音节点后重新准备。
        </div> : null}
        {prep?.issues.length ? <ul className="list-disc space-y-1 pl-5 text-sm text-destructive">
          {prep.issues.map(issue => <li key={issue}>{issue}</li>)}
        </ul> : null}

        {state.data?.tasks.map(task => <div role="status" key={task.task_id} className="flex flex-wrap items-center gap-2 text-sm">
          <span>{performanceStatus(task)}{task.error ? ` · ${task.error}` : ''}</span>
          {task.failed_dialogue_ids.length > 0 && <span>失败对白：{task.failed_dialogue_ids.join('、')}</span>}
          {['partial_failure', 'failed'].includes(task.status) && task.failed_dialogue_ids.length > 0 && <Button
            variant="outline"
            size="sm"
            disabled={busy || !preparationMatchesCurrent}
            onClick={() => retryTask(task)}
          >仅重试失败对白</Button>}
        </div>)}

        {state.data?.impact && <section aria-label="配音影响" className="space-y-1 rounded border p-3 text-sm">
          <p className="font-medium">配音影响 · 历史候选保留</p>
          <p>已有 {state.data.impact.take_count} 条候选。</p>
          {state.data.impact.stale_dialogue_ids.length > 0 && <p>对白或声音输入已变化：{state.data.impact.stale_dialogue_ids.join('、')}</p>}
          {state.data.impact.added_dialogue_ids.length > 0 && <p>新增对白：{state.data.impact.added_dialogue_ids.join('、')}</p>}
          {state.data.impact.deleted_dialogue_ids.length > 0 && <p>源对白已删除：{state.data.impact.deleted_dialogue_ids.join('、')}</p>}
          {state.data.impact.preserved_take_dialogue_ids.length > 0 && <p>已保留其历史候选：{state.data.impact.preserved_take_dialogue_ids.join('、')}</p>}
        </section>}

        <div className="flex flex-wrap gap-2">
          <Button disabled={busy || !selected.length || !state.data} onClick={() => void perform(`adopt:${JSON.stringify(selected)}:${state.data?.revision}`, async () => {
            await dubbingApi.adopt(project, episode, group, selected, state.data!.revision);
            update(nodeId, { dubbingAdoptedTakeIds: selected, dubbingGroupId: group });
            await state.refetch();
          })}>采用所选配音</Button>
          <Button variant="outline" disabled={!adoptedTakes.size} onClick={() => {
            const store = useCanvasStore.getState();
            const source = store.nodes.find(node => node.id === nodeId);
            state.data?.takes.filter(take => adoptedTakes.has(take.take_id) && take.url).forEach((take, index) => {
              if (store.nodes.some(node => node.data.dubbingTakeId === take.take_id && node.data.dubbingGroupId === group)) return;
              const target = store.addNode(CANVAS_NODE_TYPES.audio, {
                x: (source?.position.x ?? 0) + 520,
                y: (source?.position.y ?? 0) + index * 240,
              }, {
                audioUrl: take.url,
                durationMs: take.actual_duration_seconds * 1000,
                displayName: dialogues.find(line => line.dialogue_id === take.dialogue_id)?.text.slice(0, 24) ?? '已采用配音',
                referenceOnly: true,
                dubbingTakeId: take.take_id,
                dubbingDialogueId: take.dialogue_id,
                dubbingGroupId: group,
                episodeNumber: episode,
              });
              store.addEdge(nodeId, target);
            });
          }}>将已采用配音放入画布剪辑素材</Button>
        </div>
      </div>
    </DialogContent>
  </Dialog>;
}
