import { useEffect, useMemo, useRef, useState } from 'react';
import { useRouter, useRouterState } from '@tanstack/react-router';
import * as api from '@/api/agentRuns';
import {
  listAgentAdoptions,
  listPersonalAgents,
  type AgentAdoption,
  type PersonalAgent,
} from '@/api/reusableAgents';
import { useAuthStore } from '@/stores/auth-store';
import { useCanvasStore } from '@/stores/canvasStore';
import { captureFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
import {
  createRunOperation,
  readRunIntent,
  runOwnerKey,
  type RunIntent,
} from './runOperation';

export interface RuntimeNode {
  id: string;
  title: string;
}
const selectedNodes = (): RuntimeNode[] =>
  useCanvasStore
    .getState()
    .nodes.filter((n) => n.selected)
    .map((n) => ({
      id: n.id,
      title: String(
        n.data.displayName || n.data.title || n.data.label || '画布节点',
      ),
    }));

const transientFields = new Set([
  'isSizeManuallyAdjusted',
  'isGenerating',
  'isUploading',
  'generationStartedAt',
  'generationDurationMs',
  'generationError',
  'generationTaskKey',
  'generationTaskType',
  'generationTaskJobId',
  'orchestration',
  'errorMessage',
]);
function ordered(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(ordered);
  if (value && typeof value === 'object')
    return Object.fromEntries(
      Object.entries(value)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([key, item]) => [key, ordered(item)]),
    );
  return value;
}
// Keep actual input/output fields and membership, not layout or transient job UI.
// This is a local comparison fence, never a substitute for the server proof.
function inputEvidence(ids: string[], entireCanvas: boolean): string {
  const all = useCanvasStore.getState().nodes;
  const byId = new Map(all.map((node) => [node.id, node]));
  const selected = entireCanvas ? all.map((node) => node.id) : ids;
  return JSON.stringify(
    ordered(
      [...selected].sort().map((id) => {
        const node = byId.get(id);
        if (!node) return { id, missing: true };
        const ancestry = [];
        const seen = new Set([id]);
        let parentId = node.parentId;
        while (parentId) {
          const parent = byId.get(parentId);
          ancestry.push({
            id: parentId,
            type: parent?.type,
            parentId: parent?.parentId,
          });
          if (!parent || seen.has(parentId)) break;
          seen.add(parentId);
          parentId = parent.parentId;
        }
        return {
          id,
          type: node.type,
          parentId: node.parentId,
          ancestry,
          data: Object.fromEntries(
            Object.entries(node.data).filter(
              ([key]) => !transientFields.has(key),
            ),
          ),
        };
      }),
    ),
  );
}
export function useAgentRuntime(
  owner: api.RunOwner,
  enabled: boolean,
  active: boolean,
) {
  const router = useRouter();
  const location = useRouterState({ select: (s) => s.location });
  const identity = runOwnerKey(owner);
  const latest = useRef({ identity, active });
  latest.current = { identity, active };
  const instance = useMemo(
    () => ({
      identity,
      location,
      live: true,
      busy: false,
      runtime: captureFreezoneCanvasRuntime(owner.project, owner.canvas),
    }),
    [identity, location, active],
  );
  useEffect(() => {
    instance.live = true;
    return () => {
      instance.live = false;
    };
  }, [instance]);
  const current = () =>
    enabled &&
    instance.live &&
    !!instance.runtime?.isCurrent() &&
    latest.current.identity === instance.identity &&
    latest.current.active &&
    router.state.location === instance.location &&
    useAuthStore.getState().username === owner.user;
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const [main, setMain] = useState<api.MainAgent | null>(null);
  const [runtime, setRuntime] = useState<api.RuntimeStatus | null>(null);
  const [adoptions, setAdoptions] = useState<AgentAdoption[]>([]);
  const [personal, setPersonal] = useState<PersonalAgent[]>([]);
  const [builtins, setBuiltins] = useState<api.BuiltinAgent[]>([]);
  const [nodes, setNodes] = useState<RuntimeNode[]>(selectedNodes);
  const [canvasScope, setCanvasScope] = useState(false);
  const [limit, setLimit] = useState(1);
  const [specialists, setSpecialists] = useState<string[]>([]);
  const [intent, setIntent] = useState<RunIntent | null>(null);
  const [history, setHistory] = useState<
    { delegation: api.Delegation; runs: api.AgentRun[] }[]
  >([]);
  const sequence = useRef(0);
  async function refresh() {
    if (!current()) return;
    const ticket = ++sequence.current;
    try {
      const [m, r, a, p, b, delegations] = await Promise.all([
        api.getMainAgent(owner.project),
        api.getRuntime(owner.project),
        listAgentAdoptions(owner.project),
        listPersonalAgents(owner.project),
        api.listBuiltinAgents(owner.project),
        api.listDelegations(owner),
      ]);
      const items = await Promise.all(
        delegations.map(async (d) => ({
          delegation: d,
          runs: await api.listRuns(owner, d.id || d.delegation_id!),
        })),
      );
      if (!current() || ticket !== sequence.current) return;
      setMain(m);
      setRuntime(r);
      setAdoptions(a);
      setPersonal(p);
      setBuiltins(b);
      setHistory(items);
      setIntent(readRunIntent(owner));
    } catch (e) {
      if (current() && ticket === sequence.current)
        setError(e instanceof Error ? e.message : '无法读取运行状态');
    }
  }
  useEffect(() => {
    if (!enabled || !active) return;
    setWorking(false);
    void refresh();
    const interval = setInterval(() => void refresh(), 4000);
    const selection = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      if (
        current() &&
        detail?.project === owner.project &&
        detail?.canvas === owner.canvas
      ) {
        setNodes(selectedNodes());
        setCanvasScope(false);
      }
    };
    window.addEventListener('nuomi:agent-selection', selection);
    return () => {
      clearInterval(interval);
      window.removeEventListener('nuomi:agent-selection', selection);
      sequence.current++;
    };
  }, [enabled, active, instance]);
  async function execute(
    input?: Pick<RunIntent, 'id' | 'label' | 'steps'>,
    guard = current,
  ) {
    if (!guard() || instance.busy) return false;
    instance.busy = true;
    setWorking(true);
    setError('');
    try {
      const result = await createRunOperation(owner, guard).run(input);
      if (!guard()) return false;
      setIntent(readRunIntent(owner));
      if (result.status === 'busy') setError('请先恢复或处理原请求');
      await refresh();
      return guard() && result.status === 'applied';
    } catch (e) {
      if (guard())
        setError(e instanceof Error ? e.message : '操作失败，草稿已保留');
      return false;
    } finally {
      instance.busy = false;
      if (guard()) setWorking(false);
    }
  }
  async function selectMain(choice: string) {
    if (!main || !current()) return;
    const split = choice.indexOf(':');
    const source = choice.slice(0, split),
      id = choice.slice(split + 1);
    const requestId = crypto.randomUUID();
    const steps: api.RunStep[] = [];
    const existing = adoptions.find((a) => a.adoption_id === id);
    if (source === 'project' && existing)
      steps.push({
        kind: 'main',
        request: {
          request_id: requestId,
          action: 'select',
          adoption_id: id,
          expected_adoption_revision: existing.revision,
          expected_binding_revision: main.binding_revision,
        },
      });
    else {
      const entry =
        source === 'builtin'
          ? builtins.find((b) => b.source.ref.id === id)
          : personal.find((p) => p.id === id);
      if (!entry) return;
      const ref =
        'source' in entry
          ? entry.source.ref
          : { id: entry.id, revision: entry.revision };
      steps.push({
        kind: 'adopt',
        request: {
          request_id: requestId,
          action: 'adopt',
          payload: {
            source: source === 'builtin' ? 'builtin' : 'personal',
            ref,
            proof: entry.proof,
          },
        },
      });
      steps.push({
        kind: 'main',
        request: {
          request_id: crypto.randomUUID(),
          action: 'select',
          expected_binding_revision: main.binding_revision,
        },
      });
    }
    await execute({ id: requestId, label: '选择项目主创', steps });
  }
  async function send(text: string, hasAttachments: boolean) {
    if (!current() || instance.busy) return false;
    if (readRunIntent(owner)?.phase === 'pending') {
      setError('请先恢复原请求，不能重复发起运行');
      return false;
    }
    if (
      history.some((h) =>
        h.runs.some((r) =>
          ['queued', 'model_inflight', 'applying'].includes(r.state),
        ),
      )
    ) {
      setError('本会话已有运行，请先查看或停止原运行');
      return false;
    }
    if (hasAttachments) {
      setError('请先把附件放到画布，再选择相应节点；未发送或处理附件。');
      return false;
    }
    if (!runtime?.available) {
      setError('请先配置可用的文本模型，当前不能运行。');
      return false;
    }
    if (!main?.current_agent) {
      setError('请先明确选择项目主创');
      return false;
    }
    if (!canvasScope && !nodes.length) {
      setError('请选择节点，或明确选择当前画布准备范围');
      return false;
    }
    if (!text.trim()) return false;
    const captured = captureFreezoneCanvasRuntime(owner.project, owner.canvas);
    const guard = () => current() && !!captured?.isCurrent();
    const selection = structuredClone(nodes);
    const inputIds = selection.map((node) => node.id);
    const frozenInputs = inputEvidence(inputIds, canvasScope);
    const verifyInputs = () => {
      if (inputEvidence(inputIds, canvasScope) !== frozenInputs) {
        throw Error('引用内容或分组归属已变化，请重新引用并比较后发送');
      }
    };
    const selectedAgent = main.current_agent;
    const operationCapabilities: Record<string, string> = {
      'context.read': 'context.read',
      'proposal.create': 'proposal.create',
      'ontology.definition.draft': 'ontology.draft',
      'media.propose': 'media.propose',
    };
    const body = {
      canvas_id: owner.canvas,
      conversation_id: owner.conversation,
      scope: canvasScope
        ? { kind: 'canvas' }
        : { kind: 'nodes', node_ids: selection.map((n) => n.id) },
      agent: {
        kind: 'adoption',
        adoption_id: selectedAgent.adoption_id,
        revision: selectedAgent.revision,
      },
      input_refs: selection.map((n) => ({
        kind: 'canvas_node',
        canvas_id: owner.canvas,
        node_id: n.id,
      })),
      goal: text.trim(),
      allowed_operations: Object.keys(operationCapabilities).filter((op) =>
        selectedAgent.effective.capabilities.includes(
          operationCapabilities[op],
        ),
      ),
      acceptance: [text.trim()],
    };
    instance.busy = true;
    setWorking(true);
    setError('');
    try {
      if (!captured || (await captured.flush()) !== true || !guard())
        throw Error('画布尚未保存，未发送');
      verifyInputs();
      const preview = await api.previewDelegation(owner.project, body);
      if (!guard()) return false;
      verifyInputs();
      const id = crypto.randomUUID();
      const result = await createRunOperation(owner, guard).run({
        id,
        label: text.trim(),
        steps: [
          {
            kind: 'delegation',
            request: { request_id: id, ...body, preview_proof: preview.proof },
          },
          {
            kind: 'start',
            request: {
              request_id: crypto.randomUUID(),
              max_model_calls: limit,
              specialist_ids: [...specialists],
            },
          },
        ],
      });
      if (!guard()) return false;
      setIntent(readRunIntent(owner));
      await refresh();
      return guard() && result.status === 'applied';
    } catch (e) {
      if (guard())
        setError(e instanceof Error ? e.message : '结果未知，请恢复原请求');
      return false;
    } finally {
      instance.busy = false;
      if (current()) setWorking(false);
    }
  }
  async function finish() {
    if (!intent) return;
    try {
      await createRunOperation(owner, current).finish(intent.id);
      if (current()) {
        setIntent(null);
        setError('');
      }
    } catch (e) {
      if (current()) setError(String(e));
    }
  }
  async function control(run: api.AgentRun, action: 'stop' | 'resume') {
    await execute({
      id: crypto.randomUUID(),
      label: action === 'stop' ? '停止后续操作' : '恢复原运行',
      steps: [
        {
          kind: action,
          runId: run.id,
          request: {
            request_id: crypto.randomUUID(),
            expected_revision: run.revision,
          },
        },
      ],
    });
  }
  const liveRuns = history
    .flatMap((h) => h.runs)
    .filter((r) => ['queued', 'model_inflight', 'applying'].includes(r.state));
  return {
    error,
    working,
    main,
    runtime,
    adoptions,
    personal,
    builtins,
    nodes,
    setNodes,
    canvasScope,
    setCanvasScope,
    limit,
    setLimit,
    specialists,
    setSpecialists,
    intent,
    history,
    send,
    selectMain,
    finish,
    control,
    recover: () => execute(),
    refresh,
    captureSelection: () => {
      if (current()) {
        setNodes(selectedNodes());
        setCanvasScope(false);
      }
    },
    busy: working || liveRuns.length > 0,
    liveRun: liveRuns[0],
    canSend:
      current() &&
      !!runtime?.available &&
      !!main?.current_agent &&
      !working &&
      !liveRuns.length &&
      intent?.phase !== 'pending',
  };
}
