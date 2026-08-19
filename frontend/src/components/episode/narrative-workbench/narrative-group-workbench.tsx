// SPDX-License-Identifier: Elastic-2.0
import { Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { narrativeGroupVideoTaskScope, useGenerateNarrativeGroupVideo, useNarrativeGroupAction, useNarrativeGroups, useUpdateNarrativeGroupVideoDialogueSource, type NarrativeGridStage } from "@/lib/queries/narrative-groups";
import { mergeVideoModelCatalog, useMediaDefaults, useUpdateMediaDefaults, useVideoModels } from "@/lib/queries/media-models";
import { useVideoBackends } from "@/lib/queries/video";
import { DEFAULT_VIDEO_MODEL, episodeWorkbenchScopeKey, useEpisodeWorkbenchStore } from "@/stores/episode-workbench-store";
import { useTaskController } from "@/hooks/use-task-controller";
import { queryKeys } from "@/lib/query-keys";
import { NarrativeGroupList } from "./narrative-group-list";
import { GroupPipeline } from "./group-pipeline";
import { GroupVideoStage, groupFrameSummary } from "./group-video-stage";
import { GroupVideoResult } from "./group-video-result";
import { ProjectVideoModelSelect } from "./project-video-model-select";

function taskScope(response: unknown): string | undefined {
  if (!response || typeof response !== "object") return undefined;
  const record = response as Record<string, unknown>;
  if (typeof record.scope === "string") return record.scope;
  const data = record.data;
  return data && typeof data === "object" && typeof (data as Record<string, unknown>).scope === "string"
    ? (data as Record<string, unknown>).scope as string : undefined;
}

export function NarrativeGroupWorkbench({ project, episode, onRepairBeat }: { project: string; episode: number; onRepairBeat: (beatId: string) => void }) {
  const groupsQuery = useNarrativeGroups(project, episode);
  const action = useNarrativeGroupAction(project, episode);
  const modelsQuery = useVideoModels();
  const legacyModelsQuery = useVideoBackends(project);
  const defaultsQuery = useMediaDefaults(project);
  const updateDefaults = useUpdateMediaDefaults(project);
  const generateVideo = useGenerateNarrativeGroupVideo(project, episode);
  const updateDialogueSource = useUpdateNarrativeGroupVideoDialogueSource(project, episode);
  const scopeKey = episodeWorkbenchScopeKey({ project, episode });
  const selectedId = useEpisodeWorkbenchStore((s) => s.narrativeGroupSelectionByScope[scopeKey]);
  const select = useEpisodeWorkbenchStore((s) => s.setNarrativeGroupSelection);
  const mediaDefaults = defaultsQuery.data?.ok ? defaultsQuery.data.data : undefined;
  const modelId = mediaDefaults?.video_model ?? DEFAULT_VIDEO_MODEL;
  const groups = groupsQuery.data?.ok ? groupsQuery.data.data : [];
  const group = groups.find((item) => item.id === selectedId) ?? groups.find((item) => item.stages.video.status !== "completed") ?? groups[0];
  const models = mergeVideoModelCatalog(
    modelsQuery.data?.ok ? modelsQuery.data.data : [],
    legacyModelsQuery.data?.data ?? [],
  );
  const model = models.find((item) => item.id === modelId);
  const invalidateKeys = [
    queryKeys.narrativeGroups(project, episode),
    queryKeys.grids(project, episode),
    queryKeys.beats(project, episode),
  ];
  const groupVideoScope = group
    ? narrativeGroupVideoTaskScope(group.id, group.stages.video.revision)
    : undefined;
  const gridTask = useTaskController({
    key: { project, episode, taskType: "narrative_group_grid", scope: group?.id },
    invalidateKeys,
    showCompleteToast: false,
  });
  const splitTask = useTaskController({
    key: { project, episode, taskType: "narrative_group_split", scope: group?.id },
    invalidateKeys,
    showCompleteToast: false,
  });
  const groupVideoTask = useTaskController({
    key: { project, episode, taskType: "narrative_group_video", scope: groupVideoScope },
    invalidateKeys,
    showCompleteToast: false,
    onError: (error) => toast.error(`组合视频生成失败：${error}`),
  });
  if (groupsQuery.isLoading) return <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />加载叙事组…</div>;
  if (!group) return <div className="p-8 text-center text-muted-foreground">暂无叙事组，请先生成分镜 Beat。</div>;
  const runAction = async (stage: NarrativeGridStage, nextAction: "generate" | "split" | "regenerate") => {
    try {
      const response = await action.mutateAsync({ groupId: group.id, stage, action: nextAction });
      (nextAction === "split" ? splitTask : gridTask).start({ scope: taskScope(response) });
      toast.success("任务已进入队列");
    }
    catch (error) { toast.error(error instanceof Error ? error.message : "任务提交失败"); }
  };
  const runGroupVideo = async (request: { video_model: string; h3_mode: "auto" | "i2va" | "fl2va" }) => {
    try {
      const response = await generateVideo.mutateAsync({ groupId: group.id, model: request.video_model, mode: request.h3_mode, revision: group.stages.video.revision });
      groupVideoTask.start({ scope: taskScope(response) });
      toast.success("H3 导演台组合视频已进入队列");
    } catch (error) { toast.error(error instanceof Error ? error.message : "组合视频任务提交失败"); }
  };
  const frames = groupFrameSummary(group.video_inputs ?? []);
  return <div className="flex h-full min-h-0 overflow-hidden" data-narrative-group-workbench>
    <aside className="w-72 shrink-0 overflow-y-auto border-r border-white/10 p-4"><div className="mb-4 flex items-center justify-between"><h2 className="font-semibold">叙事组生产</h2><Button variant="ghost" size="icon" onClick={() => groupsQuery.refetch()}><RefreshCw className="size-4" /></Button></div><NarrativeGroupList groups={groups} selectedId={group.id} onSelect={(id) => select({ project, episode }, id)} /></aside>
    <main className="min-w-0 flex-1 overflow-y-auto p-5"><div className="mb-5 flex flex-wrap items-start justify-between gap-3"><div><div className="text-xs text-primary">叙事组 {String(group.ordinal).padStart(2, "0")}</div><h1 className="mt-1 text-xl font-semibold">{group.title || `Beat ${group.beat_ids.join("–")}`}</h1><p className="mt-1 text-xs text-muted-foreground">{group.layout.rows}×{group.layout.columns} · {group.beat_ids.length} 个 Beat · 多宫格生成后服务端自动切分</p></div><ProjectVideoModelSelect value={modelId} models={models} saving={updateDefaults.isPending} onChange={async (videoModel) => {
      try { await updateDefaults.mutateAsync({ videoModel, videoMode: mediaDefaults?.h3_mode ?? "auto" }); toast.success("项目默认视频模型已保存"); }
      catch (error) { toast.error(error instanceof Error ? error.message : "默认模型保存失败"); }
    }} /></div><GroupPipeline project={project} episode={episode} group={group} onAction={runAction} onRepairBeat={onRepairBeat} /><div className="mt-4"><GroupVideoStage modelId={modelId} mode={mediaDefaults?.h3_mode ?? model?.default_mode ?? "auto"} hasFirstFrame={frames.allHaveFirst} hasLastFrame={frames.allHaveLast} inputs={group.video_inputs} taskStatus={group.stages.video.status} available={model?.available ?? false} unavailableReason={model?.unavailable_reason} onGenerate={runGroupVideo} /><GroupVideoResult stage={group.stages.video} onDialogueSourceChange={async ({ spanIndex, dialogueSource }) => {
      try { await updateDialogueSource.mutateAsync({ groupId: group.id, spanIndex, dialogueSource, revision: group.stages.video.revision }); toast.success("已提交重新合成，对应 H3 视频不会重新生成"); }
      catch (error) { toast.error(error instanceof Error ? error.message : "对白源切换提交失败"); }
    }} /></div></main>
  </div>;
}
