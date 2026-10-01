import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import { jsonWithBackendError } from "@/lib/api-errors";
import { listStudioDocuments, saveStudioDocument } from "./studio-api";

type Stage = { revision: number; status: string; grid_asset?: string; video_asset?: string; needs_regeneration?: boolean };
type GroupImpact = { episode: number; group_id: string; shot_ids: string[]; director_revision_id: string; stages: Record<string, Stage> };
type Impact = { groups: GroupImpact[]; canvases: Array<{ canvas_id: string; node_id: string; title: string; revision: number }>; warnings: string[] };
type RefreshRecord = { purpose: "character-refresh"; character: string; group: string; episode: number; stage: "render" | "video"; baseline_revision: number; idempotency_key: string; status: string; task_id?: string };
const keyOf = (group: GroupImpact) => `${group.episode}/${group.group_id}`;

export function CharacterImpact({ project, name }: { project: string; name: string }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const impacts = useQuery({ queryKey: ["character-studio-impact", project, name], queryFn: () => jsonWithBackendError<{ data: Impact }>(api.get(p`api/v1/projects/${project}/studios/characters/${name}/impact`)), refetchInterval: 5000 });
  const records = useQuery({ queryKey: ["character-studio-refresh", project, name], queryFn: () => listStudioDocuments<RefreshRecord>(project, "character") });
  const chosen = impacts.data?.data.groups.filter((group) => selected.includes(keyOf(group))) ?? [];
  const latestRender = (group: GroupImpact) => records.data?.find((item) => item.data.purpose === "character-refresh" && item.data.character === name && item.data.group === group.group_id && item.data.episode === group.episode && item.data.stage === "render");
  const videoReady = chosen.length > 0 && chosen.every((group) => { const job = latestRender(group); return job && group.stages.render?.status === "completed" && !group.stages.render.needs_regeneration && group.stages.render.revision > job.data.baseline_revision; });
  const refresh = useMutation({ mutationFn: async (stage: "render" | "video") => {
    const outcomes: string[] = [];
    for (const group of chosen) {
      const unresolved = records.data?.find((item) => item.data.purpose === "character-refresh" && item.data.character === name && item.data.group === group.group_id && item.data.episode === group.episode && item.data.stage === stage && item.data.status === "submitting");
      if (unresolved) { outcomes.push(`${group.group_id}：之前提交结果未知，请先在任务中心核对；不重复提交付费任务。`); continue; }
      const id = crypto.randomUUID();
      const data: RefreshRecord = { purpose: "character-refresh", character: name, group: group.group_id, episode: group.episode, stage, baseline_revision: group.stages.render?.revision ?? 0, idempotency_key: id, status: "submitting" };
      const draft = await saveStudioDocument(project, "character", id, { name: `${name} · 第${group.episode}集 · ${stage}`, data, expected_revision: 0 });
      try {
        const task = await jsonWithBackendError<{ ok: boolean; task_id?: string }>(api.post(p`api/v1/projects/${project}/studios/characters/${name}/refresh-media`, { json: { episode: group.episode, group_id: group.group_id, stage, expected_render_revision: data.baseline_revision, idempotency_key: id }, retry: 0 }));
        if (!task.ok || !task.task_id) throw new Error("任务响应未知，请先检查任务中心");
        await saveStudioDocument(project, "character", id, { name: draft.name, data: { ...data, status: "submitted", task_id: task.task_id }, expected_revision: draft.revision });
        outcomes.push(`${group.group_id}：已提交 ${task.task_id}`);
      } catch (error) { outcomes.push(`${group.group_id}：${error instanceof Error ? error.message : String(error)}（记录已保留，先核对任务中心再重试）`); }
    }
    return outcomes.join("\n");
  }, onSuccess: (result) => { setMessage(result); void records.refetch(); void impacts.refetch(); }, onError: (error) => setMessage(error.message) });
  return <details className="space-y-3 rounded-xl border border-border/70 bg-card/30 p-4"><summary className="cursor-pointer text-sm font-medium">高级 · 采纳影响与选择性更新</summary>
    <p className="mt-3 text-sm text-muted-foreground">采纳形象后更新后续引用。已有素材与运行中任务保留；下方付费重生成默认不勾选。先更新选中叙事组分镜，成功后才能生成依赖视频。</p>
    <p className="text-xs text-muted-foreground">估价暂不可用，以项目模型配置和任务中心实际费用为准。画布引用需回到来源节点选择新素材，不自动覆盖。</p>
    {impacts.data?.data.groups.map((group) => <label className="flex items-start gap-2 rounded border p-2 text-sm" key={keyOf(group)}><input type="checkbox" checked={selected.includes(keyOf(group))} disabled={refresh.isPending} onChange={(event) => setSelected((items) => event.target.checked ? [...items, keyOf(group)] : items.filter((item) => item !== keyOf(group)))} /><span>第 {group.episode} 集 · {group.group_id} · 镜头 {group.shot_ids.join("、")}<br />分镜 {group.stages.render?.status ?? "未生成"} / v{group.stages.render?.revision ?? 0} · 视频 {group.stages.video?.status ?? "未生成"}</span></label>)}
    {impacts.data?.data.canvases.map((node) => <p key={`${node.canvas_id}/${node.node_id}`} className="text-xs">画布 {node.canvas_id} · 节点 {node.title} · v{node.revision}</p>)}
    {impacts.data?.data.warnings.map((warning) => <p key={warning} role="status" className="text-xs text-amber-600">{warning}</p>)}
    {impacts.data && !impacts.data.data.groups.length && !impacts.data.data.canvases.length && <p className="text-sm">未发现结构化的组或画布引用。</p>}
    <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={!chosen.length || refresh.isPending} onClick={() => refresh.mutate("render")}>重新生成选中组分镜（付费）</Button><Button variant="outline" disabled={!videoReady || refresh.isPending} onClick={() => refresh.mutate("video")}>分镜成功后生成选中组视频（付费）</Button><Button variant="ghost" onClick={() => { void impacts.refetch(); void records.refetch(); }}>刷新影响与任务</Button></div>
    {records.data?.filter((item) => item.data.purpose === "character-refresh" && item.data.character === name).slice(0, 10).map((item) => <p key={item.id} className="text-xs">{item.name} · {item.data.status} {item.data.task_id ?? "结果待核对"}</p>)}
    {message && <p role="status" className="whitespace-pre-wrap text-sm">{message}</p>}{impacts.error && <p role="alert">影响读取失败：{impacts.error.message}</p>}
  </details>;
}
