import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ClipboardEvent,
  type DragEvent,
} from 'react';
import { MessageCircle, Mic, Settings, Square, Users, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { SettingsDialog } from '@/components/settings/settings-dialog';
import { isTeamRuntime } from '@/lib/runtime-config';
import { useAuthStore } from '@/stores/auth-store';
import type { ReferenceItem } from '@/features/superchat/canvas-references';
import type { useAgentRuntime } from './useAgentRuntime';
import { useAgentDiscussion } from './useAgentDiscussion';
import {
  readSelectionDelegationDraft,
  writeSelectionDelegationDraft,
  type SelectionDelegationOwner,
} from './selectionDelegation';
import { AgentRunStatus } from './AgentRunStatus';
import { DelegationComposer } from './DelegationComposer';
import { ProjectCrewDialog } from './ProjectCrewDialog';

type Runtime = ReturnType<typeof useAgentRuntime>;
type RecognitionLike = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onend: (() => void) | null;
};

function createSpeechRecognition(): RecognitionLike | null {
  const candidate = window as unknown as {
    SpeechRecognition?: new () => RecognitionLike;
    webkitSpeechRecognition?: new () => RecognitionLike;
  };
  const Constructor = candidate.SpeechRecognition ?? candidate.webkitSpeechRecognition;
  return Constructor ? new Constructor() : null;
}

function discussionUnavailableText(code: string) {
  if (code === 'runtime_unconfigured') return '讨论模型尚未配置。配置模型后可继续讨论。';
  if (code === 'runtime_unavailable') return '当前运行方式暂不支持讨论。';
  if (code === 'project_unavailable') return '当前项目暂不可访问。';
  if (code === 'conversation_unavailable') return '当前会话暂不可访问。';
  return '当前讨论服务暂不可用。';
}

export function ManagedChatPanel({
  owner,
  runtime,
  active,
  reference,
  variant,
  onRequestClose,
}: {
  owner: SelectionDelegationOwner;
  runtime: Runtime;
  active: boolean;
  reference?: ReferenceItem;
  variant?: 'default' | 'freezone';
  onRequestClose?: () => void;
}) {
  const username = useAuthStore((state) => state.username);
  const role = useAuthStore((state) => state.role);
  const discussionOwner = useMemo(() => ({
    project: owner.project,
    canvas: owner.canvas,
    conversation: owner.conversation,
  }), [owner.canvas, owner.conversation, owner.project]);
  const discussion = useAgentDiscussion(discussionOwner, active);
  const draftOwner = useMemo<SelectionDelegationOwner>(() => ({
    user: username || owner.user,
    project: owner.project,
    canvas: owner.canvas,
    conversation: owner.conversation,
  }), [owner.canvas, owner.conversation, owner.project, owner.user, username]);
  const draftIdentity = JSON.stringify([draftOwner.user, draftOwner.project, draftOwner.canvas, draftOwner.conversation]);
  const [draftState, setDraftState] = useState(() => ({
    identity: draftIdentity,
    value: readSelectionDelegationDraft(draftOwner)?.goal ?? '',
  }));
  const draft = draftState.identity === draftIdentity
    ? draftState.value
    : readSelectionDelegationDraft(draftOwner)?.goal ?? '';
  const latestDraft = useRef({ identity: draftIdentity, value: draft });
  latestDraft.current = { identity: draftIdentity, value: draft };
  const activeRef = useRef(active);
  activeRef.current = active;
  const setDraft = useCallback((value: string) => {
    setDraftState({ identity: draftIdentity, value });
  }, [draftIdentity]);
  useEffect(() => {
    if (draftState.identity === draftIdentity) {
      writeSelectionDelegationDraft(draftOwner, { goal: draftState.value });
    }
  }, [draftIdentity, draftOwner, draftState]);

  const discussionMessages = discussion.messages;
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const delegationEntryRef = useRef<HTMLButtonElement | null>(null);
  const delegationPanelRef = useRef<HTMLDivElement | null>(null);
  const composing = useRef(false);
  const recognitionRef = useRef<RecognitionLike | null>(null);
  const speechOwnerIdentity = useRef(draftIdentity);
  const handledIntent = useRef<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [showDelegation, setShowDelegation] = useState(false);
  const showDelegationRef = useRef(showDelegation);
  showDelegationRef.current = showDelegation;
  const [crewOpen, setCrewOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [attachmentError, setAttachmentError] = useState('');
  const [draftError, setDraftError] = useState('');
  const canConfigure = !isTeamRuntime() || role === 'admin';
  const delegationRecipient = runtime.delegateAgentId === 'main'
    ? runtime.main?.current_agent?.effective.name || '项目主创未配置'
    : runtime.adoptions.find((member) => member.adoption_id === runtime.delegateAgentId)?.effective.name || '项目成员';
  const currentGrants = Object.entries(runtime.nodeOperationGrants).flatMap(([nodeId, operations]) =>
    operations.map((operation) => ({ nodeId, operation })),
  );
  const patchGrantCount = currentGrants.filter((grant) => grant.operation === 'patch').length;
  const runnableGrants = [...new Set(currentGrants.filter((grant) => grant.operation !== 'patch').map((grant) => grant.operation))];
  const permissionSummary = [
    patchGrantCount ? `可修改 ${patchGrantCount} 个节点` : '',
    runnableGrants.length ? `可运行 ${runnableGrants.join('、')}` : '',
  ].filter(Boolean).join(' · ') || '仅准备方案';
  const delegationContextKey = JSON.stringify([
    draftIdentity,
    runtime.acceptedSelectionIntent?.requestId ?? null,
    runtime.nodes.map((node) => node.id),
    runtime.delegateAgentId,
    runtime.main?.adoption_id,
    runtime.main?.current_agent?.revision,
    Object.entries(runtime.nodeOperationGrants)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([nodeId, operations]) => [nodeId, [...operations].sort()]),
    runtime.maxNodeRuns,
    runtime.limit,
    runtime.specialists,
  ]);
  const latestDelegationContext = useRef(delegationContextKey);
  latestDelegationContext.current = delegationContextKey;

  useEffect(() => {
    const container = scrollRef.current;
    if (!container) return;
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100;
    if (nearBottom) container.scrollTop = container.scrollHeight;
  }, [discussionMessages]);

  useEffect(() => {
    const intent = runtime.acceptedSelectionIntent;
    if (!intent || handledIntent.current === intent.requestId) return;
    handledIntent.current = intent.requestId;
    setShowDelegation(true);
  }, [runtime.acceptedSelectionIntent]);

  const clearDraftIfUnchanged = useCallback((submitted: string, submittedIdentity: string) => {
    const current = latestDraft.current;
    if (current.identity !== submittedIdentity || current.value !== submitted) return;
    setDraftState({ identity: submittedIdentity, value: '' });
    writeSelectionDelegationDraft(draftOwner, { goal: '' });
  }, [draftOwner]);

  const stopSpeech = useCallback((updateState = true) => {
    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    if (recognition) {
      recognition.onresult = null;
      recognition.onend = null;
      try {
        recognition.stop();
      } catch {
        // The browser can throw if recognition already ended; local state is still cleared.
      }
    }
    if (updateState) setRecording(false);
  }, []);

  useEffect(() => {
    const ownerChanged = speechOwnerIdentity.current !== draftIdentity;
    speechOwnerIdentity.current = draftIdentity;
    if (!active || ownerChanged) stopSpeech();
  }, [active, draftIdentity, stopSpeech]);

  useEffect(() => () => stopSpeech(false), [stopSpeech]);

  const sendDiscussion = useCallback(async () => {
    const original = draft;
    if (!original.trim() || discussion.busy || !active) return;
    setDraftError('');
    const sent = await discussion.send(original);
    if (sent) clearDraftIfUnchanged(original, draftIdentity);
  }, [active, clearDraftIfUnchanged, discussion, draft, draftIdentity]);

  const closeDelegation = useCallback(() => {
    setShowDelegation(false);
    requestAnimationFrame(() => delegationEntryRef.current?.focus({ preventScroll: true }));
  }, []);

  const startDelegation = useCallback(async (goal: string) => {
    const original = goal;
    const submittedContext = latestDelegationContext.current;
    const sent = await runtime.send(original, false);
    if (!sent) return false;
    const currentDraft = latestDraft.current;
    if (
      latestDelegationContext.current === submittedContext &&
      currentDraft.identity === draftIdentity &&
      currentDraft.value === original
    ) {
      clearDraftIfUnchanged(original, draftIdentity);
      if (showDelegationRef.current) closeDelegation();
    }
    return true;
  }, [clearDraftIfUnchanged, closeDelegation, draftIdentity, runtime]);

  const onComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing || composing.current || event.keyCode === 229) return;
    event.preventDefault();
    void sendDiscussion();
  };

  const reportUnsupportedFiles = (event: ClipboardEvent<HTMLTextAreaElement> | DragEvent<HTMLTextAreaElement>) => {
    const hasFiles = 'clipboardData' in event
      ? event.clipboardData.files.length > 0 || [...event.clipboardData.items].some((item) => item.kind === 'file')
      : event.dataTransfer.files.length > 0;
    if (!hasFiles) return;
    event.preventDefault();
    setAttachmentError('普通讨论暂不支持文件附件；文件没有添加。');
  };

  const toggleSpeech = () => {
    if (recording) {
      stopSpeech();
      return;
    }
    const recognition = createSpeechRecognition();
    if (!recognition) {
      setDraftError('此浏览器不支持语音输入。');
      return;
    }
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'zh-CN';
    const ownerAtStart = draftIdentity;
    recognition.onresult = (event) => {
      if (
        recognitionRef.current !== recognition ||
        !activeRef.current ||
        latestDraft.current.identity !== ownerAtStart
      ) return;
      let transcript = '';
      for (let index = 0; index < event.results.length; index += 1) transcript += event.results[index][0]?.transcript ?? '';
      setDraft(transcript);
    };
    recognition.onend = () => {
      if (recognitionRef.current !== recognition) return;
      recognitionRef.current = null;
      recognition.onresult = null;
      recognition.onend = null;
      setRecording(false);
    };
    recognitionRef.current = recognition;
    setRecording(true);
    setDraftError('');
    try {
      recognition.start();
    } catch {
      stopSpeech();
      setDraftError('无法启动语音输入，请检查浏览器权限后重试。');
    }
  };

  const onDelegationKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      closeDelegation();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = [...(delegationPanelRef.current?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [href], [tabindex]:not([tabindex="-1"])',
    ) ?? [])].filter((element) => !element.hasAttribute('hidden'));
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div className={`relative flex h-full min-h-0 flex-col overflow-hidden text-foreground ${variant === 'freezone' ? 'bg-transparent' : 'bg-[#0b0e10]'}`}>
      <header className="flex h-[58px] shrink-0 items-center gap-3 border-b border-white/[0.06] px-4">
        <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
          <MessageCircle className="size-4" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-semibold">项目助手</h2>
          <p className="truncate text-[11px] text-muted-foreground">
            {discussion.availability?.available ? '讨论可用' : discussion.loading ? '正在读取讨论能力' : '讨论能力待处理'}
            {' · '}
            {runtime.runtime?.available ? '任务运行可用' : '任务运行待配置'}
          </p>
        </div>
        <Button type="button" size="icon-sm" variant="ghost" aria-label="项目机组" title="项目机组" onClick={() => setCrewOpen(true)}>
          <Users className="size-4" aria-hidden="true" />
        </Button>
        {canConfigure && (
          <Button type="button" size="icon-sm" variant="ghost" aria-label="配置讨论模型" title="配置讨论模型" onClick={() => setSettingsOpen(true)}>
            <Settings className="size-4" aria-hidden="true" />
          </Button>
        )}
        {onRequestClose && (
          <Button type="button" size="icon-sm" variant="ghost" aria-label="关闭助手" title="关闭助手" onClick={onRequestClose}>
            <X className="size-4" aria-hidden="true" />
          </Button>
        )}
      </header>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4">
        {reference && (
          <div aria-label="已投递引用" className="mb-3 inline-flex max-w-full items-center gap-2 rounded-full bg-white/[0.05] px-3 py-1.5 text-xs text-muted-foreground">
            <span className="shrink-0">已投递引用</span>
            <span className="truncate">{reference.kind === 'asset' ? '资产' : '节点'} · {reference.label}</span>
          </div>
        )}
        {discussionMessages.length === 0 && !discussion.loading ? (
          <div className="mx-auto flex min-h-[42vh] max-w-xs flex-col items-center justify-center gap-3 text-center text-sm text-muted-foreground">
            <MessageCircle className="size-6 opacity-60" aria-hidden="true" />
            <p>可以先讨论项目内容。选择画布节点后，也能明确发起节点委托。</p>
          </div>
        ) : (
          <ol aria-label="讨论消息" className="mx-auto flex max-w-2xl flex-col gap-4">
            {discussionMessages.map((message) => (
              <li key={`${message.request_id}:${message.role}`} className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <article className={`max-w-[88%] whitespace-pre-wrap break-words rounded-2xl px-4 py-3 text-sm leading-6 ${message.role === 'user' ? 'bg-primary/15 text-foreground' : 'bg-white/[0.04] text-foreground'}`}>
                  <p>{message.message}</p>
                  {message.status !== 'completed' && (
                    <p role="status" className="mt-2 text-xs text-amber-300">
                      {message.status === 'submission_unknown' ? '提交结果尚未确认' : '等待助手回复'}
                    </p>
                  )}
                </article>
              </li>
            ))}
          </ol>
        )}
        {discussion.loading && <p role="status" className="py-3 text-center text-xs text-muted-foreground">正在读取讨论记录…</p>}
        {discussion.pendingRequests.length > 0 && (
          <div role="status" className="mx-auto mt-4 max-w-2xl rounded-lg bg-amber-300/10 px-3 py-2 text-xs text-amber-200">
            有 {discussion.pendingRequests.length} 条讨论提交尚未确认。查看当前状态；系统不会再次发送原请求。
            <Button type="button" variant="ghost" size="sm" className="ml-1" onClick={() => void discussion.refresh()}>刷新状态</Button>
          </div>
        )}
        {discussion.error && <p role="alert" className="mx-auto mt-3 max-w-2xl text-xs text-destructive">{discussion.error}</p>}
      </div>

      <AgentRunStatus runtime={runtime} compact />

      <div className="relative z-10 shrink-0 border-t border-white/[0.06] bg-[#0b0e10] px-3 pb-3 pt-2">
        <div className="mb-2 flex min-h-[34px] items-center">
          <Button
            ref={delegationEntryRef}
            type="button"
            size="sm"
            variant="outline"
            className="h-[34px] rounded-full border-white/10 px-3 text-xs focus-visible:ring-2 focus-visible:ring-primary"
            onClick={() => setShowDelegation(true)}
          >
            创建节点委托{runtime.nodes.length > 0 ? ` · ${runtime.nodes.length}` : ''}
          </Button>
          {runtime.nodes.length > 0 && (
            <span className="ml-2 min-w-0 truncate text-[11px] text-muted-foreground">
              负责人：{delegationRecipient} · {runtime.nodes.length} 个节点
              {' · '}{permissionSummary}
            </span>
          )}
        </div>
        {(draftError || attachmentError) && (
          <p role="alert" className="mb-2 px-1 text-xs text-amber-300">{draftError || attachmentError}</p>
        )}
        {discussion.availability && !discussion.availability.available && (
          <div className="mb-2 flex flex-wrap items-center gap-2 rounded-lg bg-amber-300/[0.08] px-3 py-2 text-xs text-amber-200">
            <span>{discussionUnavailableText(discussion.availability.code)}</span>
            {canConfigure && (
              <Button type="button" size="sm" variant="outline" className="h-8" onClick={() => setSettingsOpen(true)}>
                配置模型
              </Button>
            )}
          </div>
        )}
        {discussion.error && !discussion.availability && !discussion.loading && (
          <div className="mb-2 flex flex-wrap items-center gap-2 rounded-lg bg-destructive/10 px-3 py-2 text-xs">
            <span>无法读取讨论能力：{discussion.error}</span>
            <Button type="button" size="sm" variant="outline" className="h-8" onClick={() => void discussion.refresh()}>
              重试
            </Button>
          </div>
        )}
        <div className="flex items-end gap-2 rounded-2xl border border-white/10 bg-white/[0.035] p-2 focus-within:border-primary/50 focus-within:ring-1 focus-within:ring-primary/40">
          <textarea
            ref={inputRef}
            aria-label="与助手讨论"
            value={draft}
            onChange={(event) => {
              setDraft(event.target.value);
              setDraftError('');
              setAttachmentError('');
            }}
            onKeyDown={onComposerKeyDown}
            onCompositionStart={() => { composing.current = true; }}
            onCompositionEnd={() => { composing.current = false; }}
            onPaste={reportUnsupportedFiles}
            onDrop={reportUnsupportedFiles}
            onDragOver={(event) => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); }}
            placeholder="与助手讨论项目内容…"
            rows={2}
            maxLength={12000}
            className="max-h-36 min-h-12 min-w-0 flex-1 resize-y bg-transparent px-2 py-1.5 text-sm leading-6 outline-none placeholder:text-muted-foreground focus-visible:ring-0"
          />
          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            aria-label={recording ? '停止语音输入' : '语音输入'}
            title={recording ? '停止语音输入' : '语音输入'}
            onClick={toggleSpeech}
            disabled={!active}
            className={recording ? 'text-primary' : ''}
          >
            {recording ? <Square className="size-3" aria-hidden="true" /> : <Mic className="size-4" aria-hidden="true" />}
          </Button>
          <Button
            type="button"
            size="icon-sm"
            aria-label={discussion.busy ? '正在发送讨论' : '发送讨论'}
            title="发送讨论"
            disabled={!draft.trim() || discussion.busy || !active || discussion.availability?.available === false}
            onClick={() => void sendDiscussion()}
          >
            {discussion.busy ? '…' : '↑'}
          </Button>
        </div>
        <p className="mt-1 px-2 text-[10px] text-muted-foreground">Enter 发送讨论 · Shift+Enter 换行</p>
      </div>

      {showDelegation && (
        <div
          ref={delegationPanelRef}
          role="dialog"
          aria-modal="true"
          aria-label="节点委托配置"
          onKeyDown={onDelegationKeyDown}
          className="absolute inset-x-3 bottom-[112px] z-30 flex max-h-[66%] flex-col overflow-hidden rounded-2xl border border-white/10 bg-[#111518] shadow-2xl shadow-black/50"
        >
          <div className="flex shrink-0 items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
            <div className="min-w-0">
              <h3 className="text-sm font-semibold">创建节点委托</h3>
              <p className="text-[11px] text-muted-foreground">确认目标、范围和本次授权后再发起。</p>
            </div>
            <Button type="button" size="icon-sm" variant="ghost" aria-label="关闭委托配置" onClick={closeDelegation}>
              <X className="size-4" aria-hidden="true" />
            </Button>
          </div>
          <DelegationComposer
            runtime={runtime}
            goal={draft}
            onGoalChange={(value) => setDraft(value)}
            onSubmit={startDelegation}
            onClose={closeDelegation}
            onOpenCrew={() => setCrewOpen(true)}
          />
        </div>
      )}

      <ProjectCrewDialog runtime={runtime} open={crewOpen} onOpenChange={setCrewOpen} />
      {canConfigure && <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />}
    </div>
  );
}
