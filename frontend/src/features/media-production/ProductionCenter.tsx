// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  cancelProductionRun,
  createProductionRun,
  getProductionRun,
  listProductionRuns,
  pauseProductionRun,
  previewProductionRun,
  resumeProductionRun,
  retryProductionNode,
  type ProductionAuditPolicy,
  type ProductionNode,
  type ProductionNodeTypeSpec,
  type ProductionPlanAction,
  type ProductionPlanPreview,
  type ProductionRun,
  type ProductionRunDetail,
  type ProductionRunDraft,
  type ProductionScopeNode,
} from "@/api/media-production";
import { useTaskCenterStore } from "@/task-center/store";
import type { TaskState } from "@/task-center/types";
import {
  deriveDagProgress,
  deriveMissingAssets,
  deriveResourcePools,
  isProductionTask,
  productionRunId,
} from "./production-model";

const DEFAULT_NODE_TYPES = JSON.stringify(
  {
    image: {
      capability: "image.single",
      implementation: "image-primary",
      workflow_version: { id: "image", version: 1 },
      unit_cost: 0.5,
    },
    tts: {
      capability: "tts.synthesize",
      implementation: "tts-primary",
      workflow_version: { id: "speech", version: 1 },
      unit_cost: 0.4,
    },
    video: {
      capability: "video.i2va",
      implementation: "video-primary",
      workflow_version: { id: "video", version: 1 },
      unit_cost: 2,
    },
    compose: {
      capability: "media.compose",
      implementation: "ffmpeg-local",
      workflow_version: { id: "compose", version: 1 },
      unit_cost: 0.1,
    },
  },
  null,
  2,
);

const DEFAULT_SCOPE = JSON.stringify(
  {
    nodes: [
      { id: "image-1", node_type: "image", inputs: {} },
      { id: "voice-1", node_type: "tts", inputs: {} },
      { id: "video-1", node_type: "video", depends_on: ["image-1"], inputs: {} },
      {
        id: "compose-1",
        node_type: "compose",
        depends_on: ["voice-1", "video-1"],
      },
    ],
  },
  null,
  2,
);

const ACTION_LABELS: Record<ProductionPlanAction, string> = {
  create: "创建",
  reuse: "复用",
  invalidate: "失效重建",
  skip: "跳过",
};

const STATUS_LABELS: Record<string, string> = {
  pending: "等待依赖",
  ready: "就绪",
  submitting: "提交中",
  queued: "排队中",
  starting: "启动中",
  running: "运行中",
  completed: "已完成",
  succeeded: "已完成",
  failed: "失败",
  quality_failed: "质检失败",
  cancelled: "已取消",
  cancelling: "取消中",
  paused: "已暂停",
  skipped: "已跳过",
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "请求失败，请稍后重试";
}

function parseObject<T extends Record<string, unknown>>(value: string, label: string): T {
  const parsed: unknown = JSON.parse(value);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${label}必须是 JSON 对象`);
  }
  return parsed as T;
}

function taskLabel(task: TaskState): string {
  return task.current_task || task.display_name || task.task_type_label || task.task_key;
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

function cardClass(extra = ""): string {
  return `rounded-2xl border border-border/70 bg-card p-5 shadow-sm ${extra}`;
}

function SectionTitle({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="mb-4 flex items-start justify-between gap-4">
      <div>
        <h2 className="text-base font-semibold text-foreground">{title}</h2>
        {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
      </div>
    </div>
  );
}

export function ProductionCenter({ project }: { project: string }) {
  const [batchName, setBatchName] = useState("全片生产批次");
  const [budget, setBudget] = useState("10");
  const [auditPolicy, setAuditPolicy] = useState<ProductionAuditPolicy>("balanced");
  const [highCost, setHighCost] = useState(false);
  const [nodeTypesText, setNodeTypesText] = useState(DEFAULT_NODE_TYPES);
  const [scopeText, setScopeText] = useState(DEFAULT_SCOPE);
  const [artifactsText, setArtifactsText] = useState("{}");
  const [preview, setPreview] = useState<ProductionPlanPreview | null>(null);
  const [previewSignature, setPreviewSignature] = useState<string | null>(null);
  const [confirmedDraft, setConfirmedDraft] = useState<ProductionRunDraft | null>(null);
  const [runs, setRuns] = useState<ProductionRun[]>([]);
  const [detail, setDetail] = useState<ProductionRunDetail | null>(null);
  const [busy, setBusy] = useState<"preview" | "create" | "runs" | "action" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const tasksMap = useTaskCenterStore((state) => state.tasks);
  const streamHealth = useTaskCenterStore((state) => state.streamHealth);
  const productionTasks = useMemo(
    () => [...tasksMap.values()].filter(isProductionTask),
    [tasksMap],
  );
  const resourcePools = useMemo(
    () => deriveResourcePools(productionTasks),
    [productionTasks],
  );
  const selectedRunId = detail?.run.id ?? runs[0]?.id ?? null;
  const dagProgress = useMemo(
    () => deriveDagProgress(productionTasks, selectedRunId),
    [productionTasks, selectedRunId],
  );
  const missingAssets = useMemo(() => deriveMissingAssets(preview), [preview]);

  const currentSignature = useMemo(
    () =>
      JSON.stringify({
        batchName,
        budget,
        auditPolicy,
        highCost,
        nodeTypesText,
        scopeText,
        artifactsText,
      }),
    [batchName, budget, auditPolicy, highCost, nodeTypesText, scopeText, artifactsText],
  );
  const previewIsCurrent = Boolean(
    preview && confirmedDraft && previewSignature === currentSignature,
  );

  const buildDraft = useCallback((): ProductionRunDraft => {
    const parsedBudget = Number(budget);
    if (!Number.isFinite(parsedBudget) || parsedBudget < 0) {
      throw new Error("批次预算必须是大于或等于 0 的数字");
    }
    const nodeTypes = parseObject<Record<string, unknown>>(nodeTypesText, "节点类型");
    const scope = parseObject<Record<string, unknown>>(scopeText, "DAG 范围");
    const artifacts = JSON.parse(artifactsText) as unknown;
    if (!Array.isArray(scope.nodes)) throw new Error("DAG 范围必须包含 nodes 数组");
    if (!artifacts || typeof artifacts !== "object") {
      throw new Error("已有资产必须是 JSON 对象或数组");
    }
    return {
      node_types: nodeTypes as Record<string, ProductionNodeTypeSpec>,
      scope: {
        ...scope,
        audit_policy: auditPolicy,
        nodes: scope.nodes as ProductionScopeNode[],
      },
      snapshot: { batch_name: batchName.trim() || "未命名批次", budget: parsedBudget },
      existing_artifacts: artifacts as ProductionRunDraft["existing_artifacts"],
      high_cost: highCost,
    };
  }, [auditPolicy, artifactsText, batchName, budget, highCost, nodeTypesText, scopeText]);

  const loadRuns = useCallback(async () => {
    setBusy("runs");
    try {
      const page = await listProductionRuns(project);
      setRuns(page.items);
      if (page.items[0]) setDetail(await getProductionRun(project, page.items[0].id));
      else setDetail(null);
    } catch (loadError) {
      setError(errorMessage(loadError));
    } finally {
      setBusy(null);
    }
  }, [project]);

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  const handlePreview = async () => {
    setBusy("preview");
    setError(null);
    setNotice(null);
    try {
      const draft = buildDraft();
      const nextPreview = await previewProductionRun(project, draft);
      setConfirmedDraft(draft);
      setPreview(nextPreview);
      setPreviewSignature(currentSignature);
    } catch (previewError) {
      setError(errorMessage(previewError));
    } finally {
      setBusy(null);
    }
  };

  const handleCreate = async () => {
    if (!preview || !confirmedDraft || !previewIsCurrent) return;
    setBusy("create");
    setError(null);
    setNotice(null);
    try {
      const created = await createProductionRun(project, {
        draft: confirmedDraft,
        snapshotToken: preview.snapshot_token,
      });
      setDetail(created);
      setRuns((current) => [created.run, ...current.filter((run) => run.id !== created.run.id)]);
      setNotice(`批次 ${created.run.id} 已启动`);
    } catch (createError) {
      setError(errorMessage(createError));
    } finally {
      setBusy(null);
    }
  };

  const updateRun = async (action: "pause" | "resume" | "cancel") => {
    if (!detail) return;
    setBusy("action");
    setError(null);
    try {
      const fn =
        action === "pause"
          ? pauseProductionRun
          : action === "resume"
            ? resumeProductionRun
            : cancelProductionRun;
      const run = await fn(project, detail.run.id);
      setDetail((current) => (current ? { ...current, run } : current));
      setRuns((current) => current.map((item) => (item.id === run.id ? run : item)));
    } catch (actionError) {
      setError(errorMessage(actionError));
    } finally {
      setBusy(null);
    }
  };

  const retryNode = async (node: ProductionNode) => {
    if (!detail) return;
    setBusy("action");
    setError(null);
    try {
      const updated = await retryProductionNode(project, detail.run.id, node.id);
      setDetail((current) =>
        current
          ? { ...current, nodes: current.nodes.map((item) => (item.id === updated.id ? updated : item)) }
          : current,
      );
    } catch (retryError) {
      setError(errorMessage(retryError));
    } finally {
      setBusy(null);
    }
  };

  const failedNodes = detail?.nodes.filter((node) =>
    ["failed", "quality_failed", "cancelled"].includes(node.status),
  ) ?? [];

  return (
    <div className="mx-auto flex w-full max-w-[1500px] flex-col gap-5 pb-10">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.2em] text-primary">Production</p>
          <h1 className="mt-1 text-2xl font-semibold text-foreground">生产中心</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            先预览内容哈希计划，再用同一快照启动批次。
          </p>
        </div>
        <a
          href={`/projects/${encodeURIComponent(project)}/characters?tab=voices`}
          className="rounded-xl border border-border bg-background px-4 py-2 text-sm font-medium text-foreground hover:bg-muted"
        >
          审核 VoiceProfile
        </a>
      </header>

      {error ? <div role="alert" className="rounded-xl border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">{error}</div> : null}
      {notice ? <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-700">{notice}</div> : null}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(380px,0.9fr)]">
        <section className={cardClass()}>
          <SectionTitle title="批次创建" hint="配置变化后必须重新预览，旧 snapshot token 不会被复用。" />
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="grid gap-1.5 text-sm">
              <span className="text-muted-foreground">批次名称</span>
              <input value={batchName} onChange={(event) => setBatchName(event.target.value)} className="rounded-lg border border-input bg-background px-3 py-2 text-foreground" />
            </label>
            <label className="grid gap-1.5 text-sm" htmlFor="production-budget">
              <span className="text-muted-foreground">批次预算</span>
              <input id="production-budget" aria-label="批次预算" type="number" min="0" step="0.1" value={budget} onChange={(event) => setBudget(event.target.value)} className="rounded-lg border border-input bg-background px-3 py-2 text-foreground" />
            </label>
            <label className="grid gap-1.5 text-sm">
              <span className="text-muted-foreground">审核策略</span>
              <select value={auditPolicy} onChange={(event) => setAuditPolicy(event.target.value as ProductionAuditPolicy)} className="rounded-lg border border-input bg-background px-3 py-2 text-foreground">
                <option value="strict">严格</option>
                <option value="balanced">平衡</option>
                <option value="auto">自动</option>
              </select>
            </label>
            <label className="flex items-end gap-2 pb-2 text-sm text-muted-foreground">
              <input type="checkbox" checked={highCost} onChange={(event) => setHighCost(event.target.checked)} />
              高成本批次（需要管理员权限）
            </label>
          </div>
          <details className="mt-4 rounded-xl border border-border/70 bg-muted/20 p-3">
            <summary className="cursor-pointer text-sm font-medium text-foreground">高级 DAG 配置</summary>
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              <label className="grid gap-1.5 text-xs text-muted-foreground">节点类型 JSON<textarea aria-label="节点类型 JSON" value={nodeTypesText} onChange={(event) => setNodeTypesText(event.target.value)} rows={13} className="rounded-lg border border-input bg-background p-3 font-mono text-xs text-foreground" /></label>
              <label className="grid gap-1.5 text-xs text-muted-foreground">DAG 范围 JSON<textarea aria-label="DAG 范围 JSON" value={scopeText} onChange={(event) => setScopeText(event.target.value)} rows={13} className="rounded-lg border border-input bg-background p-3 font-mono text-xs text-foreground" /></label>
              <label className="grid gap-1.5 text-xs text-muted-foreground lg:col-span-2">已有资产 JSON<textarea aria-label="已有资产 JSON" value={artifactsText} onChange={(event) => setArtifactsText(event.target.value)} rows={4} className="rounded-lg border border-input bg-background p-3 font-mono text-xs text-foreground" /></label>
            </div>
          </details>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button type="button" onClick={() => void handlePreview()} disabled={busy !== null} className="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">
              {busy === "preview" ? "预览中…" : "生成预览"}
            </button>
            <button type="button" onClick={() => void handleCreate()} disabled={!previewIsCurrent || !preview?.snapshot_token || busy !== null} className="rounded-xl border border-primary px-4 py-2 text-sm font-medium text-primary disabled:opacity-40">
              {busy === "create" ? "启动中…" : "确认并启动"}
            </button>
            {preview && !previewIsCurrent ? <span className="text-xs text-amber-600">配置已变化，请重新生成预览</span> : null}
          </div>
        </section>

        <section className={cardClass()}>
          <SectionTitle title="计划预览" hint="create / reuse / invalidate / skip 均由后端确定。" />
          {!preview ? <p className="text-sm text-muted-foreground">尚未生成预览</p> : (
            <>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {(Object.keys(ACTION_LABELS) as ProductionPlanAction[]).map((action) => (
                  <div key={action} className="rounded-xl bg-muted/50 px-3 py-2 text-center text-xs text-muted-foreground">{ACTION_LABELS[action]} {preview.counts[action] ?? 0}</div>
                ))}
              </div>
              <div className="mt-4 flex items-center justify-between rounded-xl border border-border/70 px-4 py-3">
                <span className="text-sm text-muted-foreground">预计成本 / 预算</span>
                <span className="font-mono text-sm font-semibold">{preview.estimated_cost.toFixed(2)} / {Number(budget || 0).toFixed(2)}</span>
              </div>
              <div className="mt-4 overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="text-muted-foreground"><tr><th className="pb-2">节点</th><th className="pb-2">类型</th><th className="pb-2">动作</th><th className="pb-2 text-right">成本</th></tr></thead>
                  <tbody>{preview.nodes.map((node) => <tr key={node.node_id} className="border-t border-border/60"><td className="py-2 font-medium">{node.node_id}</td><td className="py-2 text-muted-foreground">{node.node_type}</td><td className="py-2"><span className="rounded-md bg-muted px-2 py-1">{ACTION_LABELS[node.action]}</span></td><td className="py-2 text-right font-mono">{node.estimated_cost.toFixed(2)}</td></tr>)}</tbody>
                </table>
              </div>
              <div className="mt-4 rounded-xl bg-amber-500/10 p-3">
                <h3 className="text-sm font-medium text-amber-700">缺失资产</h3>
                {missingAssets.length ? <ul className="mt-2 space-y-1 text-xs text-amber-800">{missingAssets.map((node) => <li key={node.node_id}>{node.node_id} · {node.node_type}</li>)}</ul> : <p className="mt-1 text-xs text-muted-foreground">无</p>}
              </div>
            </>
          )}
        </section>
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <section className={cardClass()}>
          <SectionTitle title="资源池占用" hint={`实时源：task-center · ${statusLabel(streamHealth)}`} />
          {!resourcePools.length ? <p className="text-sm text-muted-foreground">暂无活跃资源租约</p> : <div className="space-y-3">{resourcePools.map((pool) => <div key={pool.providerId}><div className="mb-1 flex justify-between text-sm"><span>{pool.providerId}</span><span className="font-mono">{pool.active} / {pool.capacity}</span></div><div className="h-2 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${pool.occupancy * 100}%` }} /></div></div>)}</div>}
        </section>
        <section className={cardClass()}>
          <SectionTitle title="DAG 进度" hint={selectedRunId ? `批次 ${selectedRunId}` : "等待批次启动"} />
          <div className="text-3xl font-semibold">{dagProgress.progress}%</div>
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-emerald-500" style={{ width: `${dagProgress.progress}%` }} /></div>
          <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs text-muted-foreground"><span>完成 {dagProgress.completed}</span><span>运行 {dagProgress.running}</span><span>失败 {dagProgress.failed}</span></div>
        </section>
        <section className={cardClass()}>
          <SectionTitle title="失败队列" hint="仅失败、质检失败或取消节点可重试。" />
          {!failedNodes.length ? <p className="text-sm text-muted-foreground">当前无失败节点</p> : <div className="space-y-2">{failedNodes.map((node) => <div key={node.id} className="flex items-center justify-between gap-3 rounded-xl border border-border/70 p-3"><div className="min-w-0"><p className="truncate text-sm font-medium">{node.node_type}</p><p className="truncate text-xs text-muted-foreground">{node.id} · {statusLabel(node.status)}</p></div><button type="button" disabled={busy !== null} onClick={() => void retryNode(node)} className="rounded-lg border border-border px-3 py-1.5 text-xs disabled:opacity-50">重试</button></div>)}</div>}
        </section>
      </div>

      <section className={cardClass()}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <SectionTitle title="实时任务" hint="直接订阅现有 task-center store，不创建额外 SSE。" />
          <div className="flex gap-2">
            {detail?.run.status === "running" ? <button type="button" onClick={() => void updateRun("pause")} disabled={busy !== null} className="rounded-lg border border-border px-3 py-1.5 text-xs">暂停</button> : null}
            {detail?.run.status === "paused" ? <button type="button" onClick={() => void updateRun("resume")} disabled={busy !== null} className="rounded-lg border border-border px-3 py-1.5 text-xs">继续</button> : null}
            {detail && !["succeeded", "failed", "cancelled", "cancelling"].includes(detail.run.status) ? <button type="button" onClick={() => void updateRun("cancel")} disabled={busy !== null} className="rounded-lg border border-destructive/50 px-3 py-1.5 text-xs text-destructive">取消</button> : null}
            <button type="button" onClick={() => void loadRuns()} disabled={busy !== null} className="rounded-lg border border-border px-3 py-1.5 text-xs">刷新批次</button>
          </div>
        </div>
        {!productionTasks.length ? <p className="text-sm text-muted-foreground">当前无生产任务</p> : <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="text-xs text-muted-foreground"><tr><th className="pb-2">任务</th><th className="pb-2">批次</th><th className="pb-2">状态</th><th className="pb-2 text-right">进度</th></tr></thead><tbody>{productionTasks.map((task) => <tr key={task.task_key} className="border-t border-border/60"><td className="py-3 font-medium">{taskLabel(task)}</td><td className="py-3 font-mono text-xs text-muted-foreground">{productionRunId(task) ?? "—"}</td><td className="py-3">{statusLabel(task.status)}</td><td className="py-3 text-right font-mono">{Math.round(task.progress)}%</td></tr>)}</tbody></table></div>}
      </section>
    </div>
  );
}
