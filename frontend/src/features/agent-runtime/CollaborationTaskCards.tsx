import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useReactFlow } from "@xyflow/react";
import { Button } from "@/components/ui/button";
import {
  authorizeCollaborationTask,
  authorizePreparedExecutionCandidate,
  controlCollaborationTask,
  getCollaborationTask,
  listCollaborationTasks,
  type CollaborationTask,
  type PreparedExecutionCandidate,
} from "@/api/collaboration";
import { useAuthStore } from "@/stores/auth-store";

const taskStateLabels: Record<string, string> = {
  awaiting_authorization: "等待你授权",
  bridging: "正在准备任务",
  delegated: "已分配给运行成员",
  queued: "等待执行",
  applying: "正在保存结果",
  model_inflight: "正在准备内容",
  waiting_children: "等待协作成员",
  paused: "已暂停",
  delivered: "已交付",
  partial: "部分完成",
  stale: "输入已变化，任务未运行",
  blocked: "需要处理",
  stopped: "已停止",
  submission_unknown: "提交结果待确认",
  failed: "执行失败",
  stale_input: "输入已变化",
  succeeded: "已完成",
  created: "已创建",
  running: "执行中",
};

const activeStates = new Set(["bridging", "delegated", "queued", "running", "applying", "model_inflight", "waiting_children"]);
const nameOf = (profile: Record<string, unknown> | undefined) =>
  typeof profile?.name === "string" ? profile.name : "会话成员";
const asRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === "object" ? value as Record<string, unknown> : null;
const asArray = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const resumeErrorMessages: Record<string, string> = {
  invalid_contract: "准备动作参数或引用范围不符合原委托授权，未继续执行。请检查引用索引和目标范围。",
  scope_violation: "准备动作超出原委托授权范围，未继续执行。请重新核对任务范围。",
  stale_input: "任务使用的画布内容已变化，未继续执行。请刷新后重新发起任务。",
  capability_denied: "固定成员没有执行这项准备动作的能力，未继续执行。",
  run_not_resumable: "当前任务没有可继续的步骤，服务端拒绝了恢复。",
  model_submission_unknown: "模型提交结果未知；系统不会重发原付费请求，请先刷新核对状态。",
  submission_unknown: "模型提交结果未知；系统不会重发原付费请求，请先刷新核对状态。",
  model_response_invalid: "已收到模型响应但内容无效；请刷新核对服务端运行状态。",
  run_busy: "运行仍在处理中，请稍后刷新状态。",
  revision_conflict: "任务状态已变化，请刷新后重新核对。",
  call_limit_reached: "已达到本次模型调用上限，服务端拒绝继续运行。",
};
const stableCode = (value: unknown): string | undefined =>
  typeof value === "string" && /^[A-Za-z][A-Za-z0-9_]{1,63}$/.test(value) ? value : undefined;
const resumeErrorCode = (error: unknown) => {
  const body = asRecord(asRecord(error)?.body);
  const detail = asRecord(body?.detail);
  const data = asRecord(body?.data);
  return [detail?.code, detail?.error_code, body?.code, body?.error_code, data?.error_code]
    .map(stableCode)
    .find((value): value is string => value !== undefined);
};
const safeResumeReason = (code: string) =>
  `${resumeErrorMessages[code] ?? "恢复未完成，请根据服务端诊断核对任务状态。"}（${code}）`;
const safeResumeError = (error: unknown) => {
  const code = resumeErrorCode(error);
  if (!code) return "恢复未完成，服务端没有确认结果。请刷新并核对任务状态。";
  return safeResumeReason(code);
};
const nonRetryableResumeErrors = new Set(["model_submission_unknown", "submission_unknown", "run_not_resumable", "call_limit_reached"]);
type CandidateAuthorizationRequest = {
  request_id: string;
  expected_revision: number;
  source_operation_id: string;
  source_request_id: string;
};
const timestampOf = (value: string) => {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : 0;
};
const newestFirst = (tasks: CollaborationTask[]) => [...tasks].sort((left, right) =>
  timestampOf(right.created_at) - timestampOf(left.created_at)
  || timestampOf(right.updated_at) - timestampOf(left.updated_at));
const humanOperation = (action: Record<string, unknown> | null | undefined) => {
  if (!action) return "";
  if (action.operation === "node.patch") return "修改指定节点内容";
  if (action.operation === "node.run") return `运行指定节点的「${String(action.node_operation ?? "操作")}」`;
  if (action.operation === "asset.design.create") return "准备角色或场景设计";
  if (action.operation === "script.document.draft") return "创建正式分集剧本草稿";
  if (action.operation === "script.document.review") return "独立审阅正式剧本版本";
  return typeof action.operation === "string" ? action.operation : "待确认操作";
};

function TaskCard({
  project,
  canvas,
  conversation,
  task: initial,
  active,
}: {
  project: string;
  canvas: string;
  conversation: string;
  task: CollaborationTask;
  active: boolean;
}) {
  const username = useAuthStore((state) => state.username);
  const queryClient = useQueryClient();
  const flow = useReactFlow();
  const [expanded, setExpanded] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [candidateConfirmations, setCandidateConfirmations] = useState<Record<string, boolean>>({});
  const [pendingRequestIds, setPendingRequestIds] = useState<Record<string, string>>({});
  const candidateRequests = useRef<Record<string, CandidateAuthorizationRequest>>({});
  const scope = { canvas_id: canvas, conversation_id: conversation };
  const listKey = ["collaboration-tasks", username, project, canvas, conversation] as const;
  const detail = useQuery({
    queryKey: [...listKey, initial.id],
    queryFn: () => getCollaborationTask(project, scope, initial.id),
    enabled: active && expanded,
    retry: false,
    refetchInterval: (query) => {
      const value = query.state.data;
      return value && (activeStates.has(value.state)
        || value.continuations?.some((item) => activeStates.has(item.state))) ? 2500 : false;
    },
  });
  const task = detail.data ?? initial;
  const run = asRecord(task.run);
  const rounds = asArray(run?.rounds);
  const children = asArray(run?.children);
  const outputs = asArray(run?.outputs);
  const nodes = task.scope.node_ids;
  const action = task.requested_action ?? null;
  const actionNode = typeof action?.node_id === "string" ? action.node_id : null;
  const requestIdFor = (kind: string) => {
    const existing = pendingRequestIds[kind];
    if (existing) return existing;
    const id = crypto.randomUUID();
    setPendingRequestIds((previous) => ({ ...previous, [kind]: id }));
    return id;
  };
  const clearRequestId = (kind: string) => setPendingRequestIds((previous) => {
    const next = { ...previous };
    delete next[kind];
    return next;
  });
  const refresh = async () => {
    setConfirmed(false);
    await queryClient.invalidateQueries({ queryKey: listKey });
    await queryClient.invalidateQueries({ queryKey: [...listKey, initial.id] });
  };
  const authorize = useMutation({
    mutationFn: () => authorizeCollaborationTask(
      project, scope, task.id, requestIdFor("authorize"), task.revision,
    ),
    onSuccess: async () => { clearRequestId("authorize"); await refresh(); },
  });
  const authorizeCandidate = useMutation({
    mutationFn: (candidate: PreparedExecutionCandidate) => {
      const candidateData = candidate.candidate;
      const key = `${candidateData.source_operation_id}:${candidateData.request_id}`;
      const storageKey = `nuomi-candidate-authorization:${username}:${project}:${task.id}:${key}`;
      let frozen: CandidateAuthorizationRequest | undefined = candidateRequests.current[key];
      if (!frozen) {
        try {
          const saved = window.localStorage.getItem(storageKey);
          if (saved) {
            const value = JSON.parse(saved) as CandidateAuthorizationRequest;
            if (value.source_operation_id === candidateData.source_operation_id
                && value.source_request_id === candidateData.request_id) frozen = value;
          }
        } catch { /* Ignore malformed local state and create a fresh explicit request. */ }
      }
      frozen ??= {
        request_id: crypto.randomUUID(), expected_revision: task.revision,
        source_operation_id: candidateData.source_operation_id,
        source_request_id: candidateData.request_id,
      };
      candidateRequests.current[key] = frozen;
      try { window.localStorage.setItem(storageKey, JSON.stringify(frozen)); } catch { /* The in-memory request still remains stable for this view. */ }
      return authorizePreparedExecutionCandidate(project, scope, task.id, frozen);
    },
    onSuccess: async (_result, candidate) => {
      const key = `${candidate.candidate.source_operation_id}:${candidate.candidate.request_id}`;
      const storageKey = `nuomi-candidate-authorization:${username}:${project}:${task.id}:${key}`;
      delete candidateRequests.current[key];
      try { window.localStorage.removeItem(storageKey); } catch { /* The completed receipt remains authoritative. */ }
      setCandidateConfirmations((previous) => ({ ...previous, [key]: false }));
      await refresh();
    },
  });
  const continuation = [...(task.continuations ?? [])].reverse().find((item) => item.run_id || item.state === "stopped");
  const controlRun = asRecord(continuation?.run) ?? run;
  const controlState = continuation?.state ?? task.state;
  const stop = useMutation({
    mutationFn: () => controlCollaborationTask(
      project, scope, task.id, "stop", requestIdFor("stop"),
      typeof controlRun?.revision === "number" ? controlRun.revision : task.revision,
      continuation?.id,
    ),
    onSuccess: async () => { clearRequestId("stop"); await refresh(); },
  });
  const resume = useMutation({
    mutationFn: () => controlCollaborationTask(
      project, scope, task.id, "resume", requestIdFor("resume"),
      typeof controlRun?.revision === "number" ? controlRun.revision : task.revision,
      continuation?.id,
    ),
    onSuccess: async () => { clearRequestId("resume"); await refresh(); },
  });
  const locate = (ids: string[]) => void flow.fitView({
    nodes: [...new Set(ids)].map((id) => ({ id })),
    padding: 0.45,
    duration: 250,
    maxZoom: 1,
  });
  const assetBindingFor = (nodeId: unknown) => {
    if (typeof nodeId !== "string") return null;
    const node = asRecord(flow.getNode(nodeId));
    const data = asRecord(node?.data);
    return asRecord(data?.designBinding);
  };
  const outputTitle = (operation: unknown, title: unknown, binding: Record<string, unknown> | null) => {
    if (operation === "asset.design.create") {
      const name = typeof binding?.name === "string" ? binding.name.trim() : "";
      return name ? `资产设计 · ${name}` : "资产设计";
    }
    if (typeof title === "string" && title.trim()) return title;
    if (operation === "script.document.draft") return "正式剧本草稿";
    if (operation === "script.document.review") return "正式剧本独立审阅";
    return typeof operation === "string" ? operation : "已记录成果";
  };
  const stateLabel = taskStateLabels[task.state] ?? task.state;
  const roundsDone = rounds.length;
  const maxCalls = typeof run?.max_model_calls === "number" ? run.max_model_calls : task.max_model_calls;
  const isStopAvailable = activeStates.has(controlState) || ["delegated", "bridging"].includes(controlState);
  const canResumeByState = ["stopped", "paused", "blocked", "submission_unknown"].includes(controlState);
  const resumeBlockedReason = continuation
    ? controlState === "submission_unknown" ? "model_submission_unknown"
      : continuation.error_code && nonRetryableResumeErrors.has(continuation.error_code)
        ? continuation.error_code : null
    : task.resume_available === false ? task.resume_blocked_reason ?? "run_not_resumable" : null;
  const runError = asRecord(run?.error);
  const taskDiagnosticCode = stableCode(runError?.code)
    ?? stableCode(task.error_code)
    ?? (canResumeByState ? stableCode(resumeBlockedReason) : undefined);
  const isResumeAvailable = canResumeByState && controlState !== "submission_unknown"
    && !resumeBlockedReason
    && !nonRetryableResumeErrors.has(resumeErrorCode(resume.error) ?? "");

  return (
    <article className="rounded-xl border border-white/10 bg-white/[0.035] p-3 text-xs">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="font-medium text-foreground">{nameOf(task.speaker.profile)} · {stateLabel}</p>
          <p className={`mt-1 whitespace-pre-wrap break-words text-foreground ${expanded ? "" : "line-clamp-3"}`}>{task.goal}</p>
          <p className="mt-2 text-muted-foreground">
            {rounds.length ? `已发起 ${roundsDone}/${maxCalls} 次模型调用` : `模型调用上限 ${task.max_model_calls} 次`}
            {children.length ? ` · ${children.length} 位协作成员` : ""}
          </p>
        </div>
        <button type="button" aria-expanded={expanded} className="shrink-0 underline" onClick={() => setExpanded((value) => !value)}>
          {expanded ? "收起" : "详情"}
        </button>
      </div>

      {action && (
        <div className="mt-3 rounded-lg border border-amber-300/20 bg-amber-300/[0.07] p-2.5">
          <p className="font-medium text-amber-100">待授权的具体操作</p>
          <p className="mt-1 text-amber-50">{humanOperation(action)}{actionNode ? ` · 节点 ${actionNode}` : ""}</p>
          {typeof action.effect === "string" && <p className="mt-1 whitespace-pre-wrap text-amber-50/80">影响：{action.effect}</p>}
          {task.state === "awaiting_authorization" && (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <label className="flex min-h-8 items-center gap-2 text-amber-50">
                <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
                我确认并授权执行上面这项操作
              </label>
              <Button type="button" size="sm" disabled={!active || !confirmed || authorize.isPending} onClick={() => authorize.mutate()}>
                {authorize.isPending ? "提交授权…" : "授权并开始"}
              </Button>
            </div>
          )}
        </div>
      )}

      {nodes.length > 0 && <button type="button" className="mt-2 min-h-8 text-left underline" onClick={() => locate(nodes)}>定位任务范围（{nodes.length} 个节点）</button>}
      {taskDiagnosticCode && <p role="alert" className="mt-2 text-amber-300">{safeResumeReason(taskDiagnosticCode)}</p>}
      {outputs.length > 0 && (
        <div className="mt-3 space-y-2 border-t border-white/10 pt-2">
          <p className="font-medium">{task.state === "partial" ? "已完成的部分成果" : "已交付成果"}</p>
          {outputs.map((rawOutput, index) => {
            const output = asRecord(rawOutput);
            const object = asRecord(output?.object);
            const payload = asRecord(object?.payload);
            const prepared = asRecord(output?.prepared_execution);
            const nodeId = typeof output?.node_id === "string" ? output.node_id
              : typeof output?.image_node_id === "string" ? output.image_node_id
                : typeof prepared?.node_id === "string" ? prepared.node_id : asRecord(output?.projection)?.node_id;
            const assetBinding = output?.operation === "asset.design.create" ? assetBindingFor(nodeId) : null;
            const title = outputTitle(output?.operation, payload?.title ?? output?.title, assetBinding);
            const rawText = output?.summary ?? payload?.text ?? output?.text ?? output?.markdown
              ?? (typeof assetBinding?.description === "string" ? assetBinding.description : undefined);
            const text = typeof rawText === "string" && output?.operation === "script.document.draft"
              ? `${rawText.slice(0, 420)}${rawText.length > 420 ? "…" : ""}` : rawText;
            const documentRef = asRecord(output?.document_ref);
            const documentId = typeof documentRef?.document_id === "string" ? documentRef.document_id : null;
            return <div key={String(output?.id ?? index)} className="rounded-md bg-black/10 p-2">
              {typeof title === "string" && <p className="font-medium">{title}</p>}
              {output?.operation === "script.document.review" && typeof output?.conclusion === "string"
                && <p className="mt-1 text-muted-foreground">审阅结论：{output.conclusion === "passed" ? "通过" : "需要修改"}</p>}
              {typeof text === "string" && <p className="mt-1 whitespace-pre-wrap text-muted-foreground">{text}</p>}
              {typeof nodeId === "string" && <button type="button" className="mt-1 underline" onClick={() => locate([nodeId])}>定位生成内容</button>}
              {documentId && <a className="mt-1 inline-block underline" href={`/projects/${encodeURIComponent(project)}/creation?document=${encodeURIComponent(documentId)}`}>打开正式剧本</a>}
            </div>;
          })}
        </div>
      )}
      {task.state === "partial" && outputs.length > 0 && <p className="mt-2 text-amber-200">已有部分成果，请展开详情核对完成项。</p>}

      {expanded && (
        <div className="mt-3 space-y-2 border-t border-white/10 pt-3">
          {detail.isLoading && <p role="status">正在读取任务详情…</p>}
          {detail.error && <p role="alert" className="text-destructive">任务详情读取失败：{detail.error instanceof Error ? detail.error.message : "请求失败"}</p>}
          {children.length > 0 && <div><p className="font-medium">协作成员进度</p><ul className="mt-1 space-y-2">{children.map((child, index) => {
            const value = asRecord(child);
            const agent = asRecord(value?.agent);
            const profile = asRecord(agent?.profile);
            const memberName = typeof agent?.name === "string" ? agent.name
              : typeof profile?.name === "string" ? profile.name
                : typeof agent?.adoption_id === "string" ? `已固定成员 ${agent.adoption_id}`
                  : typeof value?.adoption_id === "string" ? `已固定成员 ${value.adoption_id}` : "未记录的协作成员";
            const goal = typeof value?.goal === "string" ? value.goal : "";
            const unmet = typeof value?.unmet === "string" ? value.unmet
              : asArray(value?.unmet).filter((item): item is string => typeof item === "string").join("；");
            const summaries = asArray(value?.output_summaries);
            return <li key={String(value?.task_key ?? index)} className="rounded-md bg-black/10 p-2">
              <p className="font-medium">{memberName} · {taskStateLabels[String(value?.state ?? "")] ?? String(value?.state ?? "待安排")}</p>
              {goal && <p className="mt-1 text-muted-foreground">{goal}</p>}
              {unmet && <p className="mt-1 text-amber-200">待处理：{unmet}</p>}
              {summaries.map((rawSummary, outputIndex) => {
                const summary = asRecord(rawSummary);
                const prepared = asRecord(summary?.prepared_execution);
                const outputNodeId = typeof summary?.node_id === "string" ? summary.node_id
                  : typeof prepared?.node_id === "string" ? prepared.node_id : null;
                const assetBinding = summary?.operation === "asset.design.create" ? assetBindingFor(outputNodeId) : null;
                const title = outputTitle(summary?.operation, summary?.title, assetBinding);
                const text = typeof summary?.summary === "string" ? summary.summary
                  : typeof assetBinding?.description === "string" ? assetBinding.description : "";
                const conclusion = typeof summary?.conclusion === "string" ? summary.conclusion : null;
                const documentRef = asRecord(summary?.document_ref);
                const documentId = typeof documentRef?.document_id === "string" ? documentRef.document_id : null;
                return <div key={String(summary?.id ?? outputIndex)} className="mt-2 border-t border-white/10 pt-2">
                  <p>{title}</p>{text && <p className="mt-1 text-muted-foreground">{text}</p>}
                  {summary?.operation === "script.document.review" && conclusion && <p className="mt-1 text-muted-foreground">
                    审阅结论：{conclusion === "passed" ? "通过" : "需要修改"}
                  </p>}
                  {outputNodeId && <button type="button" className="mt-1 underline" onClick={() => locate([outputNodeId])}>定位生成内容</button>}
                  {documentId && <a className="mt-1 inline-block underline" href={`/projects/${encodeURIComponent(project)}/creation?document=${encodeURIComponent(documentId)}`}>打开正式剧本</a>}
                </div>;
              })}
            </li>;
          })}</ul></div>}
          {rounds.length > 0 && <p>已发起 {rounds.length} 次模型调用 / 上限 {maxCalls} 次。</p>}
          {(task.prepared_execution_candidates ?? []).map((prepared, index) => {
            const key = `${prepared.candidate.source_operation_id}:${prepared.candidate.request_id}`;
            const boundNode = asRecord(flow.getNode(prepared.candidate.node_id)?.data);
            const binding = asRecord(boundNode?.designBinding);
            const assetName = typeof binding?.name === "string" ? binding.name
              : typeof boundNode?.displayName === "string" ? boundNode.displayName : "本次准备的角色或场景";
            const continuationState = task.continuations?.find((item) =>
              item.candidate.source_operation_id === prepared.candidate.source_operation_id
              && item.candidate.request_id === prepared.candidate.request_id);
            const continuationRun = asRecord(continuationState?.run);
            const continuationOutputs = asArray(continuationRun?.outputs);
            return <div key={`${key}:${index}`} className="rounded-md border border-white/10 bg-white/[0.04] p-2.5">
              <p className="font-medium">资产设计续跑 · {assetName}</p>
              <p className="mt-1 text-muted-foreground">已准备的角色或场景图可继续生成一张，结果将投影回对应画布节点。</p>
              {prepared.status === "ready_to_authorize" && <div className="mt-2 flex flex-wrap items-center gap-2">
                <label className="flex min-h-8 items-center gap-2">
                  <input type="checkbox" checked={Boolean(candidateConfirmations[key])}
                    onChange={(event) => setCandidateConfirmations((previous) => ({ ...previous, [key]: event.target.checked }))} />
                  我确认仅对该节点执行一次正式生成
                </label>
                <Button type="button" size="sm" disabled={!active || !candidateConfirmations[key] || authorizeCandidate.isPending}
                  onClick={() => authorizeCandidate.mutate(prepared)}>
                  {authorizeCandidate.isPending ? "提交授权…" : "授权生成一张"}
                </Button>
              </div>}
              {prepared.status === "authorized" && <p className="mt-1 text-muted-foreground">已创建续跑任务{continuationState ? ` · ${taskStateLabels[continuationState.state] ?? continuationState.state}` : ""}。</p>}
              {continuationState && <div className="mt-2 rounded-md bg-black/10 p-2">
                <p className="font-medium">资产图续跑 · {taskStateLabels[continuationState.state] ?? continuationState.state}</p>
                {continuationState.error_code && <p role="alert" className="mt-1 text-amber-200">续跑状态：{continuationState.error_code}</p>}
                {continuationOutputs.map((rawOutput, outputIndex) => {
                  const output = asRecord(rawOutput);
                  if (!output) return null;
                  const projection = asRecord(output.projection);
                  const outputNodeId = typeof output.node_id === "string" ? output.node_id
                    : typeof projection?.node_id === "string" ? projection.node_id : prepared.candidate.node_id;
                  const succeeded = output.status === "succeeded";
                  return <div key={String(output.id ?? outputIndex)} className="mt-1">
                    <p className="text-muted-foreground">{succeeded ? "生成已完成，结果已写回画布节点。" : `图像生成：${taskStateLabels[String(output.status ?? "")] ?? String(output.status ?? "等待结果")}`}</p>
                    <button type="button" className="mt-1 underline" onClick={() => locate([outputNodeId])}>定位生成结果节点</button>
                  </div>;
                })}
                {continuationOutputs.length === 0 && continuationState.state === "delivered" && <>
                  <p className="mt-1 text-muted-foreground">续跑已交付，生成结果保存在画布图像节点中。</p>
                  <button type="button" className="mt-1 underline" onClick={() => locate([prepared.candidate.node_id])}>定位生成结果节点</button>
                </>}
              </div>}
              {prepared.status === "stale" && <p role="alert" className="mt-1 text-amber-200">画布内容已变化，此候选不能继续执行。</p>}
              {prepared.status === "blocked" && <p role="alert" className="mt-1 text-amber-200">当前固定成员没有执行此操作的能力，未开始生成。</p>}
            </div>;
          })}
          {authorizeCandidate.error && <p role="alert" className="text-amber-200">授权结果待确认。可以继续使用同一请求查询，系统不会自动重复提交生成。</p>}
          <details>
            <summary className="cursor-pointer text-muted-foreground">任务来源与版本信息</summary>
            <dl className="mt-2 grid gap-1 sm:grid-cols-[9rem_1fr]">
              <dt>请求目标</dt><dd>{task.user_message}</dd>
              <dt>接受条件</dt><dd>{task.acceptance.map((item) => typeof item === "string" ? item : "已记录").join("；") || "按任务目标准备"}</dd>
              <dt>会话选区版本</dt><dd>{task.scope.observed_canvas_revision ?? "未提供"}</dd>
              <dt>任务修订</dt><dd>{task.revision}</dd>
              {task.run_id && <><dt>运行记录</dt><dd>{task.run_id}</dd></>}
              {task.delegation_id && <><dt>委托记录</dt><dd>{task.delegation_id}</dd></>}
            </dl>
          </details>
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        {isStopAvailable && <Button type="button" size="sm" variant="outline" disabled={!active || stop.isPending} onClick={() => stop.mutate()}>{stop.isPending ? "正在停止…" : continuation ? "停止资产图续跑" : "停止"}</Button>}
        {isResumeAvailable && <Button type="button" size="sm" variant="outline" disabled={!active || resume.isPending} onClick={() => resume.mutate()}>{resume.isPending ? "正在恢复…" : continuation ? "检查并恢复资产图续跑" : "检查并恢复"}</Button>}
        {!taskDiagnosticCode && !isResumeAvailable && canResumeByState && resumeBlockedReason && <span role="alert" className="text-amber-200">{safeResumeReason(resumeBlockedReason)}</span>}
        {resume.error && <span role="alert" className="text-destructive">{safeResumeError(resume.error)}</span>}
        {(authorize.error || stop.error) && <span role="alert" className="text-destructive">操作未完成，请刷新后确认状态。</span>}
      </div>
    </article>
  );
}

export function CollaborationTaskCards({
  project,
  canvas,
  conversation,
  active,
}: {
  project: string;
  canvas: string;
  conversation: string;
  active: boolean;
}) {
  const username = useAuthStore((state) => state.username);
  const scope = { canvas_id: canvas, conversation_id: conversation };
  const query = useQuery({
    queryKey: ["collaboration-tasks", username, project, canvas, conversation],
    queryFn: () => listCollaborationTasks(project, scope),
    enabled: active && !!username,
    retry: false,
    refetchInterval: (current) => current.state.data?.some((task) => activeStates.has(task.state)
      || task.continuations?.some((item) => activeStates.has(item.state))) ? 2500 : false,
  });
  if (!query.data?.length && !query.isLoading && !query.isError) return null;
  return (
    <section aria-label="协作任务" className="shrink-0 border-t border-white/10 px-3 py-2">
      <div className="mb-2 flex items-center justify-between gap-2 text-xs">
        <p className="font-medium">本会话任务</p>
        <button type="button" className="text-muted-foreground underline" disabled={query.isFetching} onClick={() => void query.refetch()}>{query.isFetching ? "刷新中…" : "刷新"}</button>
      </div>
      {query.isLoading && <p role="status" className="text-xs text-muted-foreground">正在读取协作任务…</p>}
      {query.error && <p role="alert" className="text-xs text-destructive">无法读取协作任务：{query.error instanceof Error ? query.error.message : "请求失败"}</p>}
      {query.data?.length ? <div role="region" aria-label="协作任务列表" className="max-h-[22vh] min-h-0 space-y-2 overflow-y-auto overscroll-contain">
        {newestFirst(query.data).map((task) => <TaskCard key={task.id} project={project} canvas={canvas} conversation={conversation} task={task} active={active} />)}
      </div> : null}
    </section>
  );
}
