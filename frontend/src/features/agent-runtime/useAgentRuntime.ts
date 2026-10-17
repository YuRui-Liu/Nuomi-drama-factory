import { useEffect, useMemo, useRef, useState } from 'react';
import type { SetStateAction } from 'react';
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
import { getFreezoneCanvas, type FreezoneCanvasPayload } from '@/api/canvas';
import { refreshAgentCanvasChanges } from '@/features/canvas/infrastructure/orchestrationGateway';
import {
  createRunOperation,
  readDelegationCanvasBaseline,
  readRunIntent,
  runOwnerKey,
  writeDelegationCanvasBaseline,
  type RunIntent,
} from './runOperation';
import {
  consumePendingAgentSelection,
  missingSelection,
  readSelectionDelegationDraft,
  selectionChanged,
  writeSelectionDelegationDraft,
  type AgentSelectionIntent,
  type SelectionDelegationNode,
} from './selectionDelegation';

export type RuntimeNode = SelectionDelegationNode;
const parentDelegationId = (delegation: api.Delegation) =>
  delegation.parent_id ?? delegation.contract.parent_id ?? null;
const ACTIVE_RUN_STATES = ['queued', 'model_inflight', 'applying'];
export function canStopAgentRun(run: api.AgentRun): boolean {
  if (ACTIVE_RUN_STATES.includes(run.state)) return true;
  const lastRound = run.rounds[run.rounds.length - 1];
  return run.state === 'blocked' &&
    (!!run.error?.node_run_id || lastRound?.phase === 'decision_saved');
}
const isCanvasOperationOutput = (output: api.AgentRun['outputs'][number]) =>
  output.operation === 'node.patch'
    ? typeof output.node_id === 'string' && Array.isArray(output.changes)
    : output.operation === 'node.run' && output.status === 'succeeded' &&
      output.projection?.status === 'verified_projected' &&
      typeof output.projection.node_id === 'string';
const selectedNodes = (): RuntimeNode[] =>
  useCanvasStore
    .getState()
    .nodes.filter((n) => n.selected)
    .map((n) => ({
      id: n.id,
      nodeType: n.type,
      title: String(
        n.data.displayName || n.data.title || n.data.label || '画布节点',
      ),
    }));
const nodesForIds = (ids: readonly string[]): RuntimeNode[] => {
  const byId = new Map(useCanvasStore.getState().nodes.map((node) => [node.id, node]));
  return [...new Set(ids)].map((id) => {
    const node = byId.get(id);
    return node
      ? {
          id,
          nodeType: node.type,
          title: String(
            node.data.displayName || node.data.title || node.data.label || '画布节点',
          ),
        }
      : { id, title: '节点已删除或无法访问' };
  });
};

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
function inputEvidence(ids: string[]): string {
  const all = useCanvasStore.getState().nodes;
  const byId = new Map(all.map((node) => [node.id, node]));
  return JSON.stringify(
    ordered(
      [...ids].sort().map((id) => {
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
  const selectedNodeIdsKey = useCanvasStore((state) =>
    JSON.stringify(state.nodes.filter((node) => node.selected).map((node) => node.id).sort()),
  );
  const availableNodeIdsKey = useCanvasStore((state) =>
    JSON.stringify(state.nodes.map((node) => node.id).sort()),
  );
  const selectedNodeIds = useMemo(() => JSON.parse(selectedNodeIdsKey) as string[], [selectedNodeIdsKey]);
  const availableNodeIds = useMemo(() => JSON.parse(availableNodeIdsKey) as string[], [availableNodeIdsKey]);
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
  const [directoryNotice, setDirectoryNotice] = useState('');
  const [canvasSyncNotice, setCanvasSyncNotice] = useState('');
  const [working, setWorking] = useState(false);
  const [main, setMain] = useState<api.MainAgent | null>(null);
  const [runtime, setRuntime] = useState<api.RuntimeStatus | null>(null);
  const [adoptions, setAdoptions] = useState<AgentAdoption[]>([]);
  const [personal, setPersonal] = useState<PersonalAgent[]>([]);
  const [builtins, setBuiltins] = useState<api.BuiltinAgent[]>([]);
  const [delegateAgentId, setDelegateAgentId] = useState('main');
  const [nodeCapabilities, setNodeCapabilities] = useState<api.NodeCapability[]>([]);
  const [nodeOperationGrants, setNodeOperationGrants] = useState<Record<string, string[]>>({});
  const [maxNodeRuns, setMaxNodeRuns] = useState(1);
  const [selectionDraft, setSelectionDraft] = useState(() => ({
    identity,
    nodes: readSelectionDelegationDraft(owner)?.nodes ?? selectedNodes(),
  }));
  const [acceptedSelectionState, setAcceptedSelectionState] = useState<{
    identity: string;
    value: { requestId: string; nodeIds: string[] } | null;
  }>(() => ({ identity, value: null }));
  const acceptedSelectionIds = useRef(new Set<string>());
  const nodes = selectionDraft.identity === identity
    ? selectionDraft.nodes
    : readSelectionDelegationDraft(owner)?.nodes ?? selectedNodes();
  const setNodes = (next: SetStateAction<RuntimeNode[]>) => {
    setSelectionDraft((previous) => {
      const currentNodes = previous.identity === identity
        ? previous.nodes
        : readSelectionDelegationDraft(owner)?.nodes ?? selectedNodes();
      return {
        identity,
        nodes: typeof next === 'function' ? next(currentNodes) : next,
      };
    });
  };
  useEffect(() => {
    if (selectionDraft.identity === identity) {
      writeSelectionDelegationDraft(owner, { nodes: selectionDraft.nodes });
    }
  }, [identity, owner.canvas, owner.conversation, owner.project, owner.user, selectionDraft]);
  const selectionChangedFromCanvas = selectionChanged(
    nodes.map((node) => node.id),
    selectedNodeIds,
  );
  const missingNodes = missingSelection(
    nodes.map((node) => node.id),
    availableNodeIds,
  );
  const [limit, setLimit] = useState(8);
  const limitInitialized = useRef(false);
  const [specialists, setSpecialists] = useState<string[]>([]);
  const [intent, setIntent] = useState<RunIntent | null>(null);
  const [history, setHistory] = useState<
    { delegation: api.Delegation; runs: api.AgentRun[] }[]
  >([]);
  const historyRef = useRef(history);
  historyRef.current = history;
  const sequence = useRef(0);
  const nodeOperationBaselines = useRef(new Map<string, FreezoneCanvasPayload>());
  const refreshedNodeOperations = useRef(new Set<string>());
  const refreshingNodeOperations = useRef(new Set<string>());
  const acceptSelectionIntent = (selection: Pick<AgentSelectionIntent, 'requestId' | 'nodeIds'>) => {
    if (!current() || acceptedSelectionIds.current.has(selection.requestId)) return;
    acceptedSelectionIds.current.add(selection.requestId);
    setNodes(nodesForIds(selection.nodeIds));
    setNodeOperationGrants({});
    setAcceptedSelectionState({
      identity,
      value: { requestId: selection.requestId, nodeIds: [...selection.nodeIds] },
    });
  };
  async function refresh() {
    if (!current()) return;
    const ticket = ++sequence.current;
    try {
      const results = await Promise.allSettled([
        api.getMainAgent(owner.project),
        api.getRuntime(owner.project),
        listAgentAdoptions(owner.project),
        listPersonalAgents(owner.project),
        api.listBuiltinAgents(owner.project),
        api.listDelegations(owner),
        api.getNodeCapabilities(owner.project),
      ]);
      const [m, r, a, p, b, delegations, capabilities] = results;
      const failedSources = results.flatMap((result, index) =>
        result.status === 'rejected'
          ? [['项目主创', '运行配置', '项目成员', '个人 Agent', '内置岗位', '任务记录', '节点操作目录'][index]]
          : [],
      );
      const values = <T,>(result: PromiseSettledResult<T>, fallback: T): T =>
        result.status === 'fulfilled' ? result.value : fallback;
      const mainAgent = values(m, null as unknown as api.MainAgent);
      const runtimeStatus = values(r, null as unknown as api.RuntimeStatus);
      const adopted = values(a, [] as AgentAdoption[]);
      const personalAgents = values(p, [] as PersonalAgent[]);
      const builtinAgents = values(b, [] as api.BuiltinAgent[]);
      const projectDelegations = values(delegations, [] as api.Delegation[]);
      const nodeCatalog = values(capabilities, null as unknown as api.OrchestrationCapabilities | null);
      const runResults = await Promise.all(projectDelegations.map(async (d) => {
        try {
          return {
            status: 'fulfilled' as const,
            item: {
              delegation: d,
              runs: await api.listRuns(owner, d.id || d.delegation_id!),
            },
          };
        } catch {
          return { status: 'rejected' as const, delegation: d };
        }
      }));
      if (runResults.some((result) => result.status === 'rejected')) failedSources.push('运行记录');
      const previous = new Map(historyRef.current.map((item) => [
        item.delegation.id ?? item.delegation.delegation_id ?? '', item,
      ]));
      const items = runResults.map((result) => {
        if (result.status === 'fulfilled') return result.item;
        const delegation = result.delegation;
        return previous.get(delegation.id ?? delegation.delegation_id ?? '') ?? {
          delegation, runs: [],
        };
      });
      if (!current() || ticket !== sequence.current) return;
      const byId = new Map(items.map((item) => [
        item.delegation.id ?? item.delegation.delegation_id ?? '', item.delegation,
      ]));
      const rootOf = (delegation: api.Delegation) => {
        let root = delegation;
        const visited = new Set<string>();
        while (parentDelegationId(root)) {
          const id = parentDelegationId(root)!;
          if (visited.has(id)) break;
          visited.add(id);
          const parent = byId.get(id);
          if (!parent) break;
          root = parent;
        }
        return root.id ?? root.delegation_id ?? '';
      };
      for (const { delegation, runs } of items) {
        const rootId = rootOf(delegation);
        for (const run of runs) {
          for (const [index, output] of run.outputs.entries()) {
            if (!isCanvasOperationOutput(output)) continue;
            const operationKey = `${run.id}:${index}:${JSON.stringify(output.changes ?? output.after ?? output.artifacts ?? {})}`;
            if (refreshedNodeOperations.current.has(operationKey) ||
                refreshingNodeOperations.current.has(operationKey)) continue;
            if (!current() || ticket !== sequence.current) return;
            const baseline = nodeOperationBaselines.current.get(rootId) ??
              readDelegationCanvasBaseline(owner, rootId);
            if (!baseline) {
              if (current()) setCanvasSyncNotice(
                '找不到这次 Agent 操作开始前保存的画布基线；已保留本地内容，未自动同步。',
              );
              continue;
            }
            nodeOperationBaselines.current.set(rootId, baseline);
            refreshingNodeOperations.current.add(operationKey);
            try {
              const refreshResult = await refreshAgentCanvasChanges(
                owner.project,
                owner.canvas,
                baseline,
                current,
                output.after_revision ?? output.revision,
              );
              if (refreshResult.status === 'applied') {
                nodeOperationBaselines.current.set(rootId, refreshResult.baseline);
                refreshedNodeOperations.current.add(operationKey);
                if (current()) setCanvasSyncNotice(
                  writeDelegationCanvasBaseline(owner, rootId, refreshResult.baseline)
                    ? ''
                    : '画布已同步，但无法持久保存后续操作基线；请保留当前会话继续处理。',
                );
              } else if (refreshResult.status === 'conflict') {
                refreshedNodeOperations.current.add(operationKey);
                if (current()) setCanvasSyncNotice(
                  `Agent 已更新画布节点，但与本地并发修改冲突；本地编辑已保留，未同步冲突字段：${refreshResult.paths.slice(0, 4).join('、')}`,
                );
              } else if (refreshResult.status === 'pending') {
                // Keep polling until the canvas service exposes the receipt's revision.
              } else {
                if (current()) setCanvasSyncNotice('会话已切换，未应用 Agent 画布结果。');
              }
            } catch {
              if (current()) setCanvasSyncNotice('Agent 已更新画布节点，但刷新失败；本地编辑已保留，请重新打开画布检查。');
            } finally {
              refreshingNodeOperations.current.delete(operationKey);
            }
          }
        }
      }
      if (m.status === 'fulfilled') setMain(mainAgent);
      if (r.status === 'fulfilled') {
        setRuntime(runtimeStatus);
        if (!limitInitialized.current) {
          setLimit(runtimeStatus.default_model_calls);
          limitInitialized.current = true;
        }
      }
      if (a.status === 'fulfilled') setAdoptions(adopted);
      if (p.status === 'fulfilled') setPersonal(personalAgents);
      if (b.status === 'fulfilled') setBuiltins(builtinAgents);
      if (capabilities.status === 'fulfilled') setNodeCapabilities(nodeCatalog?.nodes ?? []);
      if (delegations.status === 'fulfilled') {
        historyRef.current = items;
        setHistory(items);
      }
      setIntent(readRunIntent(owner));
      setDirectoryNotice(failedSources.length
        ? `部分信息暂时无法读取（${[...new Set(failedSources)].join('、')}）；已保留其他可用信息。`
        : '');
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
      const detail = (event as CustomEvent<Partial<{
        requestId: string;
        user: string;
        project: string;
        canvas: string;
        nodeIds: string[];
      }>>).detail;
      if (
        current() &&
        detail?.project === owner.project &&
        detail?.canvas === owner.canvas &&
        (!detail.user || detail.user === owner.user)
      ) {
        const pending = consumePendingAgentSelection(owner.user, owner.project, owner.canvas);
        const nodeIds = pending?.nodeIds ?? (Array.isArray(detail.nodeIds)
          ? detail.nodeIds
          : selectedNodes().map((node) => node.id));
        const requestId = pending?.requestId ?? detail.requestId?.trim() ?? crypto.randomUUID();
        acceptSelectionIntent({ requestId, nodeIds });
      }
    };
    window.addEventListener('nuomi:agent-selection', selection);
    if (current()) {
      const pending = consumePendingAgentSelection(owner.user, owner.project, owner.canvas);
      if (pending) acceptSelectionIntent(pending);
    }
    return () => {
      clearInterval(interval);
      window.removeEventListener('nuomi:agent-selection', selection);
      sequence.current++;
    };
  }, [enabled, active, instance]);
  async function execute(
    input?: Pick<RunIntent, 'id' | 'label' | 'steps' | 'canvasBaseline'>,
    guard = current,
  ) {
    if (!guard() || instance.busy) return false;
    instance.busy = true;
    setWorking(true);
    setError('');
    try {
      const result = await createRunOperation(owner, guard).run(input);
      if (!guard()) return false;
      const recoveredDelegationId = result.intent?.steps
        .find((step) => step.kind === 'delegation')?.result?.delegation_id;
      if (typeof recoveredDelegationId === 'string' &&
          result.intent?.canvasBaseline) {
        nodeOperationBaselines.current.set(
          recoveredDelegationId,
          result.intent.canvasBaseline,
        );
      }
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
  async function adoptMember(builtinId: string) {
    if (!current() || instance.busy) return false;
    const builtin = builtins.find((item) => item.source.ref.id === builtinId);
    if (!builtin) return false;
    if (adoptions.some((item) =>
      item.source.kind === 'builtin' && item.source.ref.id === builtinId,
    )) return true;
    const requestId = crypto.randomUUID();
    return execute({
      id: requestId,
      label: `加入项目机组：${builtin.configuration.name}`,
      steps: [{
        kind: 'adopt',
        request: {
          request_id: requestId,
          action: 'adopt',
          payload: {
            source: 'builtin',
            ref: builtin.source.ref,
            proof: builtin.proof,
          },
        },
      }],
    });
  }
  function setNodeOperationGrant(nodeId: string, operation: string, enabled: boolean) {
    setNodeOperationGrants((current) => {
      const values = new Set(current[nodeId] ?? []);
      if (enabled) values.add(operation);
      else values.delete(operation);
      const next = { ...current };
      if (values.size) next[nodeId] = [...values];
      else delete next[nodeId];
      return next;
    });
  }
  function selectDelegateAgent(agentId: string) {
    setDelegateAgentId(agentId);
    setNodeOperationGrants({});
    setLimit(agentId === 'main' ? runtime?.default_model_calls ?? 8 : 1);
  }
  async function send(text: string, hasAttachments: boolean) {
    if (!current() || instance.busy) return false;
    if (readRunIntent(owner)?.phase === 'pending') {
      setError('请先恢复原请求，不能重复发起运行');
      return false;
    }
    if (
      history.some((h) =>
        h.runs.some(canStopAgentRun),
      )
    ) {
      setError('本会话已有运行，请先查看或停止原运行');
      return false;
    }
    if (!nodes.length) {
      setError('请先选择要委托的节点');
      return false;
    }
    const unavailableNodes = missingSelection(
      nodes.map((node) => node.id),
      useCanvasStore.getState().nodes.map((node) => node.id),
    );
    if (unavailableNodes.length) {
      setError(`所选节点已删除或无法访问：${unavailableNodes.join('、')}`);
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
    const directMember = adoptions.find((item) => item.adoption_id === delegateAgentId);
    const selectedAgent = delegateAgentId === 'main'
      ? main?.current_agent
      : directMember
        ? {
            adoption_id: directMember.adoption_id,
            revision: directMember.revision,
            effective: directMember.effective,
          }
        : null;
    if (!selectedAgent) {
      setError('请选择项目主创或已加入机组的专业成员');
      return false;
    }
    if (!text.trim()) return false;
    const captured = captureFreezoneCanvasRuntime(owner.project, owner.canvas);
    const guard = () => current() && !!captured?.isCurrent();
    const selection = structuredClone([...new Map(nodes.map((node) => [node.id, node])).values()]);
    const inputIds = selection.map((node) => node.id);
    const frozenInputs = inputEvidence(inputIds);
    const verifyInputs = () => {
      const missing = missingSelection(
        inputIds,
        useCanvasStore.getState().nodes.map((node) => node.id),
      );
      if (missing.length) {
        throw Error(`所选节点已删除或无法访问：${missing.join('、')}`);
      }
      if (inputEvidence(inputIds) !== frozenInputs) {
        throw Error('引用内容或分组归属已变化，请重新引用并比较后发送');
      }
    };
    const nodeById = new Map(selection.map((node) => [node.id, node]));
    const capabilitiesByType = new Map(nodeCapabilities.map((item) => [item.node_type, item]));
    const operationPairs = Object.entries(nodeOperationGrants)
      .filter(([nodeId]) => inputIds.includes(nodeId))
      .flatMap(([nodeId, operations]) => {
        const nodeType = nodeById.get(nodeId)?.nodeType;
        const available = capabilitiesByType.get(nodeType ?? '')?.operations ?? [];
        return operations
          .filter((operation) => operation === 'patch'
            ? selectedAgent.effective.capabilities.includes('node.patch')
            : selectedAgent.effective.capabilities.includes('node.run') &&
              available.some((item) => item.name === operation && item.available))
          .map((operation) => ({ node_id: nodeId, operation }));
      });
    const selectedGrantIds = [...new Set(operationPairs.map((grant) => grant.node_id))];
    const nodeRunCount = operationPairs.some((grant) => grant.operation !== 'patch')
      ? maxNodeRuns
      : 0;
    const operationCapabilities: Record<string, string> = {
      'context.read': 'context.read',
      'proposal.create': 'proposal.create',
      'ontology.definition.draft': 'ontology.draft',
      'media.propose': 'media.propose',
      'node.patch': 'node.patch',
      'node.run': 'node.run',
    };
    const body = {
      canvas_id: owner.canvas,
      conversation_id: owner.conversation,
      scope: { kind: 'nodes', node_ids: inputIds },
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
    if (operationPairs.length) {
      Object.assign(body, {
        execution_authorization: {
          allowed_node_ids: selectedGrantIds,
          allowed_node_operations: operationPairs,
          max_node_runs: nodeRunCount,
        },
      });
    }
    instance.busy = true;
    setWorking(true);
    setError('');
    try {
      if (!captured || (await captured.flush()) !== true || !guard())
        throw Error('画布尚未保存，未发送');
      verifyInputs();
      const operationBaseline = operationPairs.length
        ? await getFreezoneCanvas(owner.project, owner.canvas)
        : null;
      if (!guard()) return false;
      verifyInputs();
      const preview = await api.previewDelegation(owner.project, body);
      if (!guard()) return false;
      verifyInputs();
      const id = crypto.randomUUID();
      const result = await createRunOperation(owner, guard).run({
        id,
        label: text.trim(),
        ...(operationBaseline ? { canvasBaseline: operationBaseline } : {}),
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
              specialist_ids: directMember
                ? []
                : specialists.filter((id) => id !== selectedAgent.adoption_id),
            },
          },
        ],
      });
      if (!guard()) return false;
      if (operationBaseline) {
        const delegationResult = result.intent?.steps.find((step) => step.kind === 'delegation')?.result;
        const delegationId = String(delegationResult?.delegation_id ?? '');
        if (delegationId) nodeOperationBaselines.current.set(delegationId, operationBaseline);
      }
      if (operationPairs.length && result.status === 'applied') setCanvasSyncNotice('');
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
    .filter(canStopAgentRun);
  return {
    error,
    working,
    main,
    runtime,
    adoptions,
    personal,
    builtins,
    nodeCapabilities,
    canvasSyncNotice,
    directoryNotice,
    nodes,
    setNodes,
    selectionChanged: selectionChangedFromCanvas,
    missingNodes,
    acceptedSelectionIntent: acceptedSelectionState.identity === identity
      ? acceptedSelectionState.value
      : null,
    limit,
    setLimit,
    specialists,
    setSpecialists,
    nodeOperationGrants,
    setNodeOperationGrant,
    maxNodeRuns,
    setMaxNodeRuns,
    delegateAgentId,
    selectDelegateAgent,
    intent,
    history,
    send,
    selectMain,
    adoptMember,
    finish,
    control,
    recover: () => execute(),
    refresh,
    captureSelection: () => {
      if (current()) {
        setNodes(selectedNodes());
        setNodeOperationGrants({});
      }
    },
    busy: working || liveRuns.length > 0,
    liveRun: liveRuns[0],
    canSend:
      current() &&
      !!runtime?.available &&
      (delegateAgentId === 'main'
        ? !!main?.current_agent
        : adoptions.some((item) => item.adoption_id === delegateAgentId)) &&
      !working &&
      !liveRuns.length &&
      intent?.phase !== 'pending',
  };
}
