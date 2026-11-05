import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter, useRouterState } from '@tanstack/react-router';
import * as runApi from '@/api/agentRuns';
import {
  adoptIdeation,
  editIdeation,
  getIdeation,
  getIdeationRequest,
  previewIdeation,
  type IdeationCandidate,
  type IdeationCandidateBody,
  type IdeationObject,
  type IdeationScope,
} from '@/api/ideation';
import { listAgentAdoptions } from '@/api/reusableAgents';
import { api } from '@/lib/api';
import { jsonWithBackendError } from '@/lib/api-errors';
import { useAuthStore } from '@/stores/auth-store';
import { useCanvasStore } from '@/stores/canvasStore';
import {
  captureFreezoneCanvasRuntime,
  currentFreezoneCanvasScope,
} from '@/features/freezone/canvasSyncRuntime';
import {
  activeConversationKey,
  conversationEndpoint,
  type ConversationOwner,
} from '@/features/superchat/conversation-scope';
import {
  createConversation,
  readCreation,
  type AssistantConversation,
} from '@/features/superchat/conversation-creation';
import { CANVAS_NODE_TYPES, type AgentNodeData, type CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { createRunOperation, readRunIntent, runOwnerKey, type RunIntent } from '@/features/agent-runtime/runOperation';
import { executeIdeationWrite, readPendingIdeationWrite, type IdeationWriteIntent } from './writeIntent';
import { agentInputSignature, collectAgentNodeInputs, isAgentRunStale, type AgentInputRef } from './runtimeModel';

export interface AgentNodeOutputView {
  output: runApi.AgentRun['outputs'][number];
  object?: IdeationObject;
  parseError?: string;
  rawText?: string;
}

export interface AgentNodeHistoryItem {
  delegation: runApi.Delegation;
  run: runApi.AgentRun;
  outputs: AgentNodeOutputView[];
  inputSignature?: string;
}

interface Props {
  nodeId: string;
  agentRef: AgentNodeData['agentRef'];
  saveSettings: () => Promise<boolean>;
}

interface ConversationList {
  data: AssistantConversation[];
}

const ACTIVE_STATES = new Set(['queued', 'model_inflight', 'applying']);
const CANDIDATE_FIELDS = ['title', 'premise', 'protagonist_goal', 'conflict', 'hook', 'expansion'] as const;

function message(error: unknown): string {
  return error instanceof Error && error.message ? error.message : String(error);
}

function validIdeation(value: IdeationObject): boolean {
  return value.candidates.length === 3 && value.candidates.every((candidate) =>
    candidate.id.trim() && CANDIDATE_FIELDS.every((field) => typeof candidate[field] === 'string' && candidate[field].trim()),
  );
}

function sourceFor(delegation: runApi.Delegation) {
  const agent = delegation.contract.agent;
  return agent.fixed?.source ?? agent.source;
}

function hasTarget(delegation: runApi.Delegation, nodeId: string): boolean {
  const expanded = delegation.contract.expanded_scope as { node_ids?: string[]; nodes?: { node_id: string }[] };
  return expanded.node_ids?.includes(nodeId) === true || expanded.nodes?.some((node) => node.node_id === nodeId) === true;
}

function persistedInputSignature(owner: runApi.RunOwner, runId: string): string | undefined {
  const activeKey = `agent-run-intent:v1:${runOwnerKey(owner)}`;
  const keys = Object.keys(localStorage).filter((key) => key === activeKey || key.startsWith(`${activeKey}:archive:`));
  for (const key of keys.reverse()) {
    try {
      const intent = JSON.parse(localStorage.getItem(key) ?? 'null') as RunIntent | null;
      if (!intent || runOwnerKey(intent.owner) !== runOwnerKey(owner)) continue;
      const step = intent.steps.find((entry) => entry.kind === 'start');
      const result = step?.result as Record<string, unknown> | undefined;
      const nestedRun = result?.run as Record<string, unknown> | undefined;
      const resultId = nestedRun?.id ?? result?.id;
      if (resultId === runId && typeof step?.meta?.inputSignature === 'string') return step.meta.inputSignature;
    } catch {
      // Ignore unrelated or damaged archived intents; they cannot authorize a write.
    }
  }
  return undefined;
}

export function useAgentNodeRuntime({ nodeId, agentRef, saveSettings }: Props) {
  const router = useRouter();
  const location = useRouterState({ select: (state) => state.location });
  const user = useAuthStore((state) => state.username) || '';
  const canvasScope = currentFreezoneCanvasScope();
  const project = canvasScope?.project ?? '';
  const canvasId = canvasScope?.canvasId ?? '';
  const hydrationEpoch = useCanvasStore((state) => state.canvasHydrationEpoch);
  const identity = JSON.stringify([user, project, canvasId, nodeId, agentRef.id, agentRef.revision, hydrationEpoch]);
  const instance = useMemo(() => ({
    identity,
    user,
    project,
    canvasId,
    location,
    hydrationEpoch,
    live: true,
    runtime: project && canvasId ? captureFreezoneCanvasRuntime(project, canvasId) : null,
  }), [identity, location]);
  const latest = useRef({ identity, user, location, saveSettings });
  latest.current = { identity, user, location, saveSettings };
  const busy = useRef(false);
  const sequence = useRef(0);
  const storeNodes = useCanvasStore((state) => state.nodes);
  const storeEdges = useCanvasStore((state) => state.edges);
  const node = storeNodes.find((item) => item.id === nodeId && item.type === CANVAS_NODE_TYPES.agent);
  const nodeData = node?.data as AgentNodeData | undefined;
  const incoming = useMemo(
    () => project && canvasId
      ? collectAgentNodeInputs(storeNodes, storeEdges, nodeId, canvasId)
      : { refs: [], unadoptedAgentNodeIds: [] },
    [storeNodes, storeEdges, nodeId, project, canvasId],
  );
  const [working, setWorking] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [runtimeStatus, setRuntimeStatus] = useState<runApi.RuntimeStatus | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(nodeData?.conversationId || null);
  const [history, setHistory] = useState<AgentNodeHistoryItem[]>([]);
  const [pendingIntent, setPendingIntent] = useState<RunIntent | null>(null);
  const [pendingWrite, setPendingWrite] = useState<IdeationWriteIntent | null>(null);
  const [maxModelCalls, setMaxModelCalls] = useState(1);

  useEffect(() => {
    instance.live = true;
    return () => { instance.live = false; };
  }, [instance]);

  const current = useCallback(() => {
    const scope = currentFreezoneCanvasScope();
    const state = useCanvasStore.getState();
    const latestNode = state.nodes.find((item) => item.id === nodeId);
    const latestData = latestNode?.data as AgentNodeData | undefined;
    return instance.live
      && instance.identity === latest.current.identity
      && instance.user === latest.current.user
      && instance.user !== ''
      && useAuthStore.getState().username === instance.user
      && router.state.location === instance.location
      && latest.current.location === instance.location
      && Boolean(instance.runtime?.isCurrent())
      && scope?.project === instance.project
      && scope.canvasId === instance.canvasId
      && state.canvasHydrationEpoch === instance.hydrationEpoch
      && latestNode?.type === CANVAS_NODE_TYPES.agent
      && latestData?.agentRef?.id === agentRef.id
      && latestData.agentRef.revision === agentRef.revision;
  }, [agentRef.id, agentRef.revision, instance, nodeId, router]);

  const ownerFor = useCallback((conversation: string): runApi.RunOwner => ({
    user: instance.user,
    project: instance.project,
    canvas: instance.canvasId,
    conversation,
  }), [instance]);

  const isSameSemanticInput = useCallback((signature: string, refs: AgentInputRef[]) => {
    const state = useCanvasStore.getState();
    const latestNode = state.nodes.find((item) => item.id === nodeId && item.type === CANVAS_NODE_TYPES.agent);
    if (!latestNode) return false;
    const latestRefs = collectAgentNodeInputs(state.nodes, state.edges, nodeId, instance.canvasId).refs;
    if (JSON.stringify(latestRefs) !== JSON.stringify(refs)) return false;
    return agentInputSignature(latestNode, latestRefs, state.nodes) === signature;
  }, [instance.canvasId, nodeId]);

  const savePatch = useCallback(async (patch: Partial<AgentNodeData>): Promise<boolean> => {
    if (!current() || !instance.runtime) return false;
    const before = useCanvasStore.getState().nodes.find((item) => item.id === nodeId);
    if (!before || before.type !== CANVAS_NODE_TYPES.agent) return false;
    useCanvasStore.getState().updateNodeData(nodeId, patch);
    const flushed = await instance.runtime.flush();
    if (!current()) return false;
    if (flushed === true) return true;
    const currentNode = useCanvasStore.getState().nodes.find((item) => item.id === nodeId);
    const currentData = currentNode?.data as AgentNodeData | undefined;
    const beforeData = before.data as AgentNodeData;
    const rollback: Partial<AgentNodeData> = {};
    for (const key of Object.keys(patch) as (keyof AgentNodeData)[]) {
      if (JSON.stringify(currentData?.[key]) === JSON.stringify(patch[key])) {
        (rollback as Record<string, unknown>)[key] = beforeData[key];
      }
    }
    if (Object.keys(rollback).length) useCanvasStore.getState().updateNodeData(nodeId, rollback);
    return false;
  }, [current, instance, nodeId]);

  const persistConversation = useCallback(async (id: string): Promise<boolean> => {
    if (!current()) return false;
    const existing = useCanvasStore.getState().nodes.find((item) => item.id === nodeId)?.data as AgentNodeData | undefined;
    if (existing?.conversationId !== id && !(await savePatch({ conversationId: id }))) {
      throw new Error('会话已找到，但画布未保存会话引用，请重试');
    }
    if (!current()) return false;
    setConversationId(id);
    try { localStorage.setItem(activeConversationKey({ user: instance.user, project: instance.project, canvas: instance.canvasId }), id); }
    catch { /* The canvas reference remains the durable selection. */ }
    return true;
  }, [current, instance, nodeId, savePatch]);

  const listConversations = useCallback(async (): Promise<AssistantConversation[]> => {
    const owner: ConversationOwner = { user: instance.user, project: instance.project, canvas: instance.canvasId };
    const response = await jsonWithBackendError<ConversationList>(api.get(conversationEndpoint(owner)));
    if (!Array.isArray(response.data)) throw new Error('画布会话列表格式无效');
    return response.data.filter((item) =>
      item.id !== 'default' && item.canvas_id === instance.canvasId &&
      (!('project_id' in item) || (item as AssistantConversation & { project_id?: string }).project_id === instance.project),
    );
  }, [instance]);

  const resolveConversation = useCallback(async (createIfEmpty: boolean): Promise<string | null> => {
    if (!current()) throw new Error('画布或账号已切换');
    const items = await listConversations();
    if (!current()) throw new Error('画布或账号已切换');
    const currentNode = useCanvasStore.getState().nodes.find((item) => item.id === nodeId);
    const savedId = (currentNode?.data as AgentNodeData | undefined)?.conversationId;
    let selected = savedId && items.some((item) => item.id === savedId) ? savedId : null;
    if (!selected) {
      try {
        const active = localStorage.getItem(activeConversationKey({ user: instance.user, project: instance.project, canvas: instance.canvasId }));
        if (active && items.some((item) => item.id === active)) selected = active;
      } catch { /* Fall back to the newest canvas conversation. */ }
    }
    if (!selected) {
      selected = [...items].sort((a, b) => Date.parse(b.updated_at || '') - Date.parse(a.updated_at || ''))[0]?.id ?? null;
    }
    if (!selected && createIfEmpty) {
      const owner: ConversationOwner = { user: instance.user, project: instance.project, canvas: instance.canvasId };
      let pendingCreation;
      try { pendingCreation = readCreation(owner); }
      catch (failure) { throw failure; }
      const created = await createConversation(owner, pendingCreation ? undefined : '画布 Agent 会话', current);
      if (!current()) throw new Error('会话已创建，但画布已切换');
      if (!created.id || created.canvas_id !== instance.canvasId) throw new Error('新会话归属未确认，不能开始运行');
      selected = created.id;
    }
    if (selected) await persistConversation(selected);
    else setConversationId(null);
    return selected;
  }, [current, instance, listConversations, nodeId, persistConversation]);

  const refresh = useCallback(async () => {
    if (!current()) return;
    const ticket = ++sequence.current;
    setLoading(true);
    try {
      const runtimePromise = runApi.getRuntime(instance.project).then((value) => {
        if (current() && ticket === sequence.current) setRuntimeStatus(value);
      });
      let selectedConversation: string | null = null;
      try { selectedConversation = await resolveConversation(false); }
      catch (failure) { if (current() && ticket === sequence.current) setError(message(failure)); }
      if (selectedConversation && current() && ticket === sequence.current) {
        const owner = ownerFor(selectedConversation);
        const delegations = await runApi.listDelegations(owner);
        const matching = delegations.filter((delegation) => {
          const source = sourceFor(delegation);
          return hasTarget(delegation, nodeId) && source?.kind === 'builtin'
            && source.ref?.id === agentRef.id && source.ref.revision === agentRef.revision;
        });
        const items = await Promise.all(matching.map(async (delegation) => {
          const delegationId = delegation.delegation_id || delegation.id;
          if (!delegationId) return [];
          const runs = await runApi.listRuns(owner, delegationId);
          return Promise.all(runs.map(async (run) => {
            const existingRef = ((useCanvasStore.getState().nodes.find((item) => item.id === nodeId)?.data as AgentNodeData | undefined)?.runRefs ?? []).find((ref) => ref.id === run.id);
            const outputs = await Promise.all(run.outputs
              .filter((output) => output.target_node_ids?.includes(nodeId))
              .map(async (output): Promise<AgentNodeOutputView> => {
                if (agentRef.id !== 'builtin:ideation' || output.operation !== 'proposal.create' || !output.object_ref?.id) return { output };
                const objectId = existingRef?.objectId ?? output.object_ref.id;
                const revision = existingRef?.objectRevision ?? output.object_ref.revision;
                const rawText = output.object?.payload?.text;
                try {
                  const object = await getIdeation(instance.project, objectId, {
                    canvas_id: instance.canvasId,
                    conversation_id: selectedConversation!,
                    node_id: nodeId,
                    revision,
                  });
                  return validIdeation(object)
                    ? { output, object, rawText }
                    : { output, object, parseError: '提案没有通过三候选结构校验。可以展开查看原始文本。', rawText };
                } catch (failure) {
                  return { output, parseError: message(failure), rawText };
                }
              }));
            return outputs.length || run.state ? [{ delegation, run, outputs, inputSignature: existingRef?.inputSignature ?? persistedInputSignature(owner, run.id) }] : [];
          }));
        }));
        if (!current() || ticket !== sequence.current) return;
        setHistory(items.flat(2).sort((a, b) => b.run.id.localeCompare(a.run.id)));
      } else if (current() && ticket === sequence.current) {
        setHistory([]);
      }
      await runtimePromise;
      if (!current() || ticket !== sequence.current) return;
      setPendingIntent(selectedConversation ? readRunIntent(ownerFor(selectedConversation)) : null);
      setPendingWrite(selectedConversation ? readPendingIdeationWrite(ownerFor(selectedConversation), nodeId) : null);
      setError('');
    } catch (failure) {
      if (current() && ticket === sequence.current) setError(message(failure));
    } finally {
      if (current() && ticket === sequence.current) setLoading(false);
    }
  }, [agentRef.id, agentRef.revision, current, instance, nodeId, ownerFor, resolveConversation]);

  useEffect(() => {
    if (!project || !canvasId || !user) return;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => {
      window.clearInterval(timer);
      sequence.current += 1;
    };
  }, [canvasId, project, refresh, user]);

  const persistRunRef = useCallback(async (intent: RunIntent): Promise<boolean> => {
    if (!current()) return false;
    const delegationStep = intent.steps.find((step) => step.kind === 'delegation');
    const startStep = intent.steps.find((step) => step.kind === 'start');
    const run = (startStep?.result?.run ?? startStep?.result) as Partial<runApi.AgentRun> | undefined;
    const delegationId = String(delegationStep?.result?.delegation_id ?? startStep?.delegationId ?? '');
    if (!run?.id || !delegationId || !startStep?.meta?.inputSignature) return false;
    const ref: NonNullable<AgentNodeData['runRefs']>[number] = {
      id: run.id,
      revision: typeof run.revision === 'number' ? run.revision : 1,
      state: run.state || 'queued',
      delegationId,
      inputSignature: startStep.meta.inputSignature,
      agentId: startStep.meta.agentId,
      agentRevision: startStep.meta.agentRevision,
    };
    const existing = useCanvasStore.getState().nodes.find((item) => item.id === nodeId)?.data as AgentNodeData | undefined;
    const runRefs = (existing?.runRefs ?? []).filter((item) => item.id !== ref.id).concat(ref);
    return savePatch({ runRefs });
  }, [current, nodeId, savePatch]);

  const resumePendingIntent = useCallback(async (owner: runApi.RunOwner): Promise<boolean> => {
    const pending = readRunIntent(owner);
    if (!pending || pending.phase !== 'pending') return false;
    const result = await createRunOperation(owner, current).run();
    if (!current()) return false;
    if (result.status === 'applied' && result.intent) {
      if (result.intent.steps.some((step) => step.kind === 'start') && !(await persistRunRef(result.intent))) {
        await refresh();
        setError('Agent 运行已创建，但画布未保存运行引用。运行仍可在历史记录中查看；请保存画布后刷新。');
        return false;
      }
      setNotice(result.intent.steps.some((step) => step.kind === 'adopt')
        ? '已恢复 Agent 版本采用。再次点击“生成”后才会启动运行。'
        : '已恢复原运行请求；没有创建新的模型请求。');
    } else {
      setError(result.intent?.error || '原请求仍待处理，请稍后重试恢复');
    }
    setPendingIntent(readRunIntent(owner));
    await refresh();
    return result.status === 'applied';
  }, [current, persistRunRef, refresh]);

  const generate = useCallback(async (allowUnknownRepeat = false) => {
    if (busy.current) return false;
    busy.current = true;
    setWorking(true);
    setError('');
    setNotice('');
    try {
      if (!(await latest.current.saveSettings())) throw new Error('节点设定尚未保存，未启动运行');
      if (!current()) throw new Error('画布或节点已切换');
      const ensuredConversation = await resolveConversation(true);
      if (!ensuredConversation) throw new Error('当前画布还没有可用会话');
      const owner = ownerFor(ensuredConversation);
      const pending = readRunIntent(owner);
      if (pending?.phase === 'pending') {
        await resumePendingIntent(owner);
        return false;
      }
      const unknownRun = history.find((item) => item.run.state === 'submission_unknown');
      if (unknownRun && !allowUnknownRepeat) {
        throw new Error('上次模型调用结果未知。请刷新运行记录；若仍要新建一次运行，请使用“仍然新建运行”并确认可能重复。');
      }
      const [runtime, builtin, adoptions] = await Promise.all([
        runApi.getRuntime(instance.project),
        runApi.getBuiltinAgentRevision(instance.project, agentRef.id, agentRef.revision),
        listAgentAdoptions(instance.project),
      ]);
      if (!current()) return false;
      setRuntimeStatus(runtime);
      if (!runtime.available) throw new Error(`当前文本运行不可用：${runtime.code}`);
      if (builtin.source.ref.id !== agentRef.id || builtin.source.ref.revision !== agentRef.revision) {
        throw new Error(`内置 Agent ${agentRef.id}@${agentRef.revision} 已不可用，请重新选择版本`);
      }
      const existing = adoptions.find((item) =>
        item.source.kind === 'builtin' && item.source.ref.id === agentRef.id &&
        item.source.ref.revision === agentRef.revision && Object.keys(item.overrides ?? {}).length === 0,
      );
      let adoptionId = existing?.adoption_id;
      let adoptionRevision = existing?.revision;
      if (!adoptionId || !adoptionRevision) {
        const requestId = crypto.randomUUID();
        const result = await createRunOperation(owner, current).run({
          id: requestId,
          label: '采用节点 Agent 版本',
          steps: [{
            kind: 'adopt',
            request: {
              request_id: requestId,
              action: 'adopt',
              payload: { source: 'builtin', ref: builtin.source.ref, proof: builtin.proof },
            },
          }],
        });
        if (!current()) return false;
        const adopted = result.intent?.steps[0]?.result as Partial<runApi.BuiltinAgent & { adoption_id: string; revision: number }> | undefined;
        if (result.status !== 'applied' || !adopted?.adoption_id || !adopted.revision) {
          setPendingIntent(readRunIntent(owner));
          throw new Error(result.intent?.error || 'Agent 版本尚未确认采用。恢复原请求后，再明确点击“生成”。');
        }
        adoptionId = adopted.adoption_id;
        adoptionRevision = adopted.revision;
        setNotice('Agent 版本已固定，正在准备本次运行。');
      }

      const active = history.find((item) => ACTIVE_STATES.has(item.run.state));
      if (active) throw new Error(`本节点已有运行处于“${active.run.state}”，请先恢复或停止该运行`);
      const currentNode = useCanvasStore.getState().nodes.find((item) => item.id === nodeId && item.type === CANVAS_NODE_TYPES.agent);
      const currentData = currentNode?.data as AgentNodeData | undefined;
      if (!currentNode || !currentData || currentData.agentRef.id !== agentRef.id || currentData.agentRef.revision !== agentRef.revision) {
        throw new Error('节点 Agent 版本已变化，请重新打开节点');
      }
      const settings = structuredClone(currentData.settings);
      if (JSON.stringify(settings) !== JSON.stringify((useCanvasStore.getState().nodes.find((item) => item.id === nodeId)?.data as AgentNodeData | undefined)?.settings)) {
        throw new Error('节点设定已变化，请重新保存后运行');
      }
      const state = useCanvasStore.getState();
      const captured = collectAgentNodeInputs(state.nodes, state.edges, nodeId, instance.canvasId);
      const refs = captured.refs;
      const signature = agentInputSignature(currentNode, refs, state.nodes);
      const isIdeation = agentRef.id === 'builtin:ideation';
      const task = settings.idea.trim();
      if (!isIdeation && !task) throw new Error('请先填写本次文本任务');
      const body = {
        canvas_id: instance.canvasId,
        conversation_id: ensuredConversation,
        scope: { kind: 'nodes', node_ids: [nodeId] },
        agent: { kind: 'adoption', adoption_id: adoptionId, revision: adoptionRevision },
        input_refs: refs,
        goal: isIdeation
          ? `依据设定为当前节点提出恰好三条彼此有区分度的原创故事方向。立意设定(JSON)：${JSON.stringify(settings)}。proposal.create 的 text 必须为无 Markdown 代码围栏的纯 JSON，根对象仅含 schema_version 与 candidates；schema_version 为整数 1；candidates 恰好三条；每条含唯一非空 id 与六个非空字符串字段 title、premise、protagonist_goal、conflict、hook、expansion。只提供可编辑候选，不自动采纳。`
          : `为当前 ${currentData.displayName || 'Agent'} 节点准备普通文本创作建议。用户任务：${task}`,
        allowed_operations: ['context.read', 'proposal.create'],
        acceptance: isIdeation
          ? ['严格提交三条立意候选，遵守 JSON 字段合同。', '候选仅作为 proposal.create 文本建议，不自动采纳或执行媒体制作。']
          : [task, '通过 proposal.create 提交真实、完整、可编辑的文本建议，不自动采纳或执行媒体制作。'],
      };
      const preview = await runApi.previewDelegation(instance.project, body);
      if (!current()) return false;
      if (!isSameSemanticInput(signature, refs)) throw new Error('节点设定或引用已变化，请重新生成预览');
      const delegationRequestId = crypto.randomUUID();
      const startRequestId = crypto.randomUUID();
      const result = await createRunOperation(owner, current).run({
        id: delegationRequestId,
        label: isIdeation ? '生成三条立意候选' : task,
        steps: [
          {
            kind: 'delegation',
            meta: { inputSignature: signature, agentId: agentRef.id, agentRevision: agentRef.revision },
            request: { request_id: delegationRequestId, ...body, preview_proof: preview.proof },
          },
          {
            kind: 'start',
            meta: { inputSignature: signature, agentId: agentRef.id, agentRevision: agentRef.revision },
            request: { request_id: startRequestId, max_model_calls: maxModelCalls, specialist_ids: [] },
          },
        ],
      });
      if (!current()) return false;
      if (result.status !== 'applied' || !result.intent) {
        setPendingIntent(readRunIntent(owner));
        throw new Error(result.intent?.error || '运行请求尚未确认，恢复原请求后再继续');
      }
      const runRefSaved = await persistRunRef(result.intent);
      if (!runRefSaved) {
        await refresh();
        throw new Error('Agent 运行已创建，但画布未保存运行引用。运行仍可在历史记录中查看；请保存画布后刷新。');
      }
      await refresh();
      return true;
    } catch (failure) {
      if (current()) setError(message(failure));
      return false;
    } finally {
      busy.current = false;
      if (current()) setWorking(false);
    }
  }, [agentRef.id, agentRef.revision, current, history, instance.canvasId, instance.project, isSameSemanticInput, maxModelCalls, nodeId, ownerFor, persistRunRef, refresh, resolveConversation, resumePendingIntent]);

  const controlRun = useCallback(async (item: AgentNodeHistoryItem, action: 'stop' | 'resume') => {
    if (busy.current) return false;
    const owner = ownerFor(item.delegation.contract.conversation_id as string || conversationId || '');
    busy.current = true;
    setWorking(true);
    setError('');
    try {
      const result = await createRunOperation(owner, current).run({
        id: crypto.randomUUID(),
        label: `${action === 'stop' ? '停止' : '恢复'}Agent运行`,
        steps: [{
          kind: action,
          runId: item.run.id,
          request: { request_id: crypto.randomUUID(), expected_revision: item.run.revision },
        }],
      });
      if (!current()) return false;
      if (result.status !== 'applied') throw new Error(result.intent?.error || '运行状态更新未确认');
      await refresh();
      return true;
    } catch (failure) {
      if (current()) setError(message(failure));
      return false;
    } finally {
      busy.current = false;
      if (current()) setWorking(false);
    }
  }, [conversationId, current, ownerFor, refresh]);

  const writeCandidate = useCallback(async (
    action: 'edit' | 'adopt',
    item: AgentNodeHistoryItem,
    object: IdeationObject,
    candidate: IdeationCandidate,
    edited?: IdeationCandidateBody,
  ) => {
    if (busy.current) return false;
    busy.current = true;
    setWorking(true);
    setError('');
    setNotice('');
    const owner = ownerFor(conversationId || '');
    const scope: IdeationScope = { canvas_id: instance.canvasId, conversation_id: conversationId || '', node_id: nodeId };
    try {
      const result = await executeIdeationWrite({
        owner,
        nodeId,
        prepare: async () => {
          const latestObject = await getIdeation(instance.project, object.object_id, scope);
          if (latestObject.revision !== object.revision) throw new Error('候选版本已变化，先刷新后再保存；当前编辑草稿已保留');
          if (!latestObject.candidates.some((entry) => entry.id === candidate.id)) throw new Error('候选已不存在，请刷新后比较');
          const previewBody = {
            ...scope,
            action,
            expected_revision: latestObject.revision,
            candidate_id: candidate.id,
            ...(action === 'edit' && edited ? { candidate: edited } : {}),
          } as const;
          const preview = await previewIdeation(instance.project, latestObject.object_id, previewBody);
          if (!current()) throw new Error('画布或账号已切换，未提交候选写入');
          return {
            action,
            objectId: latestObject.object_id,
            runId: item.run.id,
            body: {
              ...scope,
              request_id: crypto.randomUUID(),
              expected_revision: latestObject.revision,
              candidate_id: candidate.id,
              proof: preview.proof,
              ...(action === 'edit' && edited ? { candidate: edited } : {}),
            },
          };
        },
      }, {
        getReceipt: async (writeOwner, targetNodeId, _objectId, requestId) => {
          try { return await getIdeationRequest(instance.project, requestId, { canvas_id: instance.canvasId, conversation_id: writeOwner.conversation, node_id: targetNodeId }); }
          catch (failure) {
            if ((failure as { status?: number }).status === 404) return null;
            throw failure;
          }
        },
        send: (_writeOwner, _targetNodeId, objectId, writeAction, requestBody) => writeAction === 'edit'
          ? editIdeation(instance.project, objectId, requestBody as never)
          : adoptIdeation(instance.project, objectId, requestBody as never),
        persist: async (intent, receipt) => {
          if (!current()) return false;
          const patch: Partial<AgentNodeData> = {};
          const ref = receipt.object_ref;
          if (intent.action === 'edit' && ref && intent.runId) {
            const data = useCanvasStore.getState().nodes.find((entry) => entry.id === nodeId)?.data as AgentNodeData | undefined;
            const runRefs = (data?.runRefs ?? []).map((runRef) => runRef.id === intent.runId
              ? { ...runRef, objectId: ref.object_id, objectRevision: ref.revision }
              : runRef);
            patch.runRefs = runRefs;
          }
          if (intent.action === 'adopt' && ref) {
            const body = intent.body;
            patch.adoptedRef = {
              kind: 'ideation_selection',
              request_id: String(body.request_id),
              canvas_id: String(body.canvas_id),
              conversation_id: String(body.conversation_id),
              node_id: String(body.node_id),
              object_id: ref.object_id,
              revision: ref.revision,
              candidate_id: String(body.candidate_id),
            };
          }
          if (!Object.keys(patch).length) return false;
          return savePatch(patch);
        },
      });
      if (!current()) return false;
      setPendingWrite(readPendingIdeationWrite(owner, nodeId));
      if (result.status === 'unknown') throw new Error('候选写入结果未知，点击“恢复候选写入”会先查询原回执');
      if (result.status === 'flush_failed') throw new Error('候选回执已保存，但画布引用未保存；恢复时只会重放这条回执');
      setNotice(result.status === 'recovered_previous'
        ? '已恢复上一条候选写入。若还要执行新的修改，请再次保存或采纳。'
        : action === 'adopt' ? '已明确采纳此候选版本；下游只会引用这个固定版本。' : '已保存新的候选版本。');
      await refresh();
      return true;
    } catch (failure) {
      if (current()) {
        setPendingWrite(readPendingIdeationWrite(owner, nodeId));
        setError(message(failure));
      }
      return false;
    } finally {
      busy.current = false;
      if (current()) setWorking(false);
    }
  }, [conversationId, current, instance.canvasId, instance.project, nodeId, ownerFor, refresh, savePatch]);

  const recoverCandidateWrite = useCallback(async () => {
    const intent = pendingWrite;
    if (!intent) return false;
    const reference = history.find((item) => item.run.id === intent.runId);
    const output = reference?.outputs.find((entry) => entry.object?.object_id === intent.objectId);
    const candidate = output?.object?.candidates.find((entry) => entry.id === intent.body.candidate_id);
    if (!reference || !output?.object || !candidate) {
      setError('找到未完成写入，但当前画布还没有对应候选详情；先刷新候选后再恢复');
      return false;
    }
    return writeCandidate(intent.action, reference, output.object, candidate, intent.action === 'edit'
      ? intent.body.candidate as IdeationCandidateBody
      : undefined);
  }, [history, pendingWrite, writeCandidate]);

  const currentSignature = useMemo(() => {
    if (!node || !project || !canvasId) return '';
    return agentInputSignature(node, incoming.refs, storeNodes);
  }, [canvasId, incoming.refs, node, project, storeNodes]);
  const isStale = useCallback((item: AgentNodeHistoryItem) =>
    isAgentRunStale(item.inputSignature, agentInputSignature(
      (useCanvasStore.getState().nodes.find((entry) => entry.id === nodeId) ?? node) as CanvasNode,
      collectAgentNodeInputs(useCanvasStore.getState().nodes, useCanvasStore.getState().edges, nodeId, instance.canvasId).refs,
      useCanvasStore.getState().nodes,
    )), [instance.canvasId, node, nodeId]);

  return {
    working,
    loading,
    error,
    notice,
    runtimeStatus,
    conversationId,
    history,
    pendingIntent,
    pendingWrite,
    maxModelCalls,
    setMaxModelCalls,
    incoming,
    nodes: storeNodes,
    currentSignature,
    generate,
    refresh,
    resumePending: () => conversationId ? resumePendingIntent(ownerFor(conversationId)) : Promise.resolve(false),
    stopRun: (item: AgentNodeHistoryItem) => controlRun(item, 'stop'),
    resumeRun: (item: AgentNodeHistoryItem) => controlRun(item, 'resume'),
    saveCandidateEdit: (item: AgentNodeHistoryItem, object: IdeationObject, candidate: IdeationCandidate, body: IdeationCandidateBody) => writeCandidate('edit', item, object, candidate, body),
    adoptCandidate: (item: AgentNodeHistoryItem, object: IdeationObject, candidate: IdeationCandidate) => writeCandidate('adopt', item, object, candidate),
    recoverCandidateWrite,
    isStale,
    setError,
  };
}
