// SPDX-License-Identifier: Elastic-2.0
import { Loader2, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { narrativeGroupTaskScope, narrativeGroupVideoTaskScope, updateNarrativeGroupVideoPlan, useGenerateNarrativeGroupVideo, useNarrativeGroupAction, useNarrativeGroupReferences, useNarrativeGroups, useUpdateNarrativeGroupVideoDialogueSource, type NarrativeGridStage, type NarrativeGroupGenerationSelection } from "@/lib/queries/narrative-groups";
import { availableVideoModels, resolveVideoModel, useMediaDefaults, useUpdateMediaDefaults, useVideoModels } from "@/lib/queries/media-models";
import { episodeWorkbenchScopeKey, useEpisodeWorkbenchStore } from "@/stores/episode-workbench-store";
import { useTaskController } from "@/hooks/use-task-controller";
import { queryKeys } from "@/lib/query-keys";
import { aspectRatioForOrientation, type Orientation } from "@/lib/aspect-ratio";
import { useUpdateProject } from "@/lib/queries/projects";
import { NarrativeGroupList } from "./narrative-group-list";
import { GroupPipeline } from "./group-pipeline";
import { GroupVideoStage, groupFrameSummary } from "./group-video-stage";
import { GroupVideoResult } from "./group-video-result";
import { GroupReferenceDialog } from "./group-reference-dialog";
import { useProjectAspectRatio } from "@/stores/aspect-ratio-store";
import { NarrativeAspectSelector } from "./narrative-aspect-selector";
import { ProjectVideoModelSelect } from "./project-video-model-select";

function taskScope(response: unknown): string | undefined {
  if (!response || typeof response !== "object") return undefined;
  const record = response as Record<string, unknown>;
  if (typeof record.scope === "string") return record.scope;
  const data = record.data;
  return data && typeof data === "object" && typeof (data as Record<string, unknown>).scope === "string"
    ? (data as Record<string, unknown>).scope as string : undefined;
}

function activeStageScope(
  group: { id: string; stages: Record<NarrativeGridStage, { status: string; revision: number }> } | undefined,
  fallback: NarrativeGridStage,
): string | undefined {
  if (!group) return undefined;
  const stage = (["sketch", "render"] as const).find(
    (name) => group.stages[name].status === "queued" || group.stages[name].status === "running",
  ) ?? fallback;
  return narrativeGroupTaskScope(group.id, stage, group.stages[stage].revision);
}

export function NarrativeGroupWorkbench({ project, episode, onRepairBeat }: { project: string; episode: number; onRepairBeat: (beatId: string) => void }) {
  const { orientation, setOrientation } = useProjectAspectRatio(project);
  const updateProject = useUpdateProject(project);
  const aspectRatio = orientation === "landscape" ? "16:9" as const : "9:16" as const;
  const groupsQuery = useNarrativeGroups(project, episode);
  const action = useNarrativeGroupAction(project, episode);
  const modelsQuery = useVideoModels();
  const defaultsQuery = useMediaDefaults(project);
  const updateDefaults = useUpdateMediaDefaults(project);
  const [pendingAction, setPendingAction] = useState<{ groupId: string; stage: NarrativeGridStage; action: "generate" | "regenerate" } | null>(null);
  const [videoPlanSaving, setVideoPlanSaving] = useState(false);
  const [selectedVideoModelId, setSelectedVideoModelId] = useState<string | null>(null);
  const generateVideo = useGenerateNarrativeGroupVideo(project, episode);
  const updateDialogueSource = useUpdateNarrativeGroupVideoDialogueSource(project, episode);
  const scopeKey = episodeWorkbenchScopeKey({ project, episode });
  const selectedId = useEpisodeWorkbenchStore((s) => s.narrativeGroupSelectionByScope[scopeKey]);
  const select = useEpisodeWorkbenchStore((s) => s.setNarrativeGroupSelection);
  const mediaDefaults = defaultsQuery.data?.ok ? defaultsQuery.data.data : undefined;
  const groups = groupsQuery.data?.ok ? groupsQuery.data.data : [];
  const group = groups.find((item) => item.id === selectedId) ?? groups.find((item) => item.stages.video.status !== "completed") ?? groups[0];
  const referencesQuery = useNarrativeGroupReferences(project, episode, pendingAction?.groupId ?? "", pendingAction?.stage ?? "render", pendingAction !== null);
  const catalog = modelsQuery.data?.ok ? modelsQuery.data.data : [];
  const models = availableVideoModels(catalog);
  const model = resolveVideoModel(selectedVideoModelId ?? mediaDefaults?.video_model, catalog);
  const modelId = model?.id ?? "";
  const invalidateKeys = [
    queryKeys.narrativeGroups(project, episode),
    queryKeys.grids(project, episode),
    queryKeys.beats(project, episode),
  ];
  const groupGridScope = activeStageScope(group, "sketch");
  const groupSplitScope = activeStageScope(group, "render");
  const groupVideoScope = group
    ? narrativeGroupVideoTaskScope(group.id, group.stages.video.revision)
    : undefined;
  const gridTask = useTaskController({
    key: { project, episode, taskType: "narrative_group_grid", scope: groupGridScope },
    invalidateKeys,
    showCompleteToast: false,
  });
  const splitTask = useTaskController({
    key: { project, episode, taskType: "narrative_group_split", scope: groupSplitScope },
    invalidateKeys,
    showCompleteToast: false,
  });
  useEffect(() => { setPendingAction(null); }, [project, episode, group?.id]);
  useEffect(() => { setSelectedVideoModelId(null); }, [project]);
  const groupVideoTask = useTaskController({
    key: { project, episode, taskType: "narrative_group_video", scope: groupVideoScope },
    invalidateKeys,
    showCompleteToast: false,
    onError: (error) => toast.error(`组合视频生成失败：${error}`),
  });
  const changeAspect = async (nextOrientation: Orientation) => {
    if (nextOrientation === orientation || updateProject.isPending) return;
    const previousOrientation = orientation;
    setOrientation(nextOrientation);
    try {
      await updateProject.mutateAsync({
        aspect_ratio: aspectRatioForOrientation(nextOrientation),
      });
      toast.success("项目画幅已保存");
    } catch (error) {
      setOrientation(previousOrientation);
      toast.error(error instanceof Error ? error.message : "项目画幅保存失败");
    }
  };
  const aspectSelector = <NarrativeAspectSelector orientation={orientation} saving={updateProject.isPending} onChange={changeAspect} />;
  if (groupsQuery.isLoading) return <div className="flex h-full flex-col"><div className="flex justify-end border-b border-white/10 p-4">{aspectSelector}</div><div className="flex items-center gap-2 p-6 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />加载叙事组…</div></div>;
  if (!group) return <div className="flex h-full flex-col"><div className="flex justify-end border-b border-white/10 p-4">{aspectSelector}</div><div className="p-8 text-center text-muted-foreground">暂无叙事组，请先生成分镜 Beat。</div></div>;
  const runAction = async (stage: NarrativeGridStage, nextAction: "generate" | "split" | "regenerate") => {
    if (nextAction !== "split") {
      setPendingAction({ groupId: group.id, stage, action: nextAction });
      return;
    }
    try {
      const response = await action.mutateAsync({
        groupId: group.id,
        stage,
        action: nextAction,
        aspectRatio,
      });
      (nextAction === "split" ? splitTask : gridTask).start({ scope: taskScope(response) });
      toast.success("任务已进入队列");
    }
    catch (error) { toast.error(error instanceof Error ? error.message : "任务提交失败"); }
  };
  const confirmAction = async (selection: NarrativeGroupGenerationSelection) => {
    if (!pendingAction) return;
    try {
      if (selection.saveAsProjectDefault) {
        await updateDefaults.mutateAsync({
          videoModel: modelId,
          videoMode: mediaDefaults?.h3_mode ?? "auto",
          narrativeSketchProvider: pendingAction.stage === "sketch" ? (selection.providerId ?? "grsai-main") : mediaDefaults?.narrative_sketch_provider,
          narrativeSketchModel: pendingAction.stage === "sketch" ? (selection.model ?? "nano-banana-2") : mediaDefaults?.narrative_sketch_model,
          narrativeRenderProvider: pendingAction.stage === "render" ? (selection.providerId ?? "grsai-main") : mediaDefaults?.narrative_render_provider,
          narrativeRenderModel: pendingAction.stage === "render" ? (selection.model ?? "gpt-image-2") : mediaDefaults?.narrative_render_model,
        });
      }
      const response = await action.mutateAsync({ ...pendingAction, aspectRatio, selection });
      gridTask.start({ scope: taskScope(response) });
      toast.success("任务已进入队列");
      setPendingAction(null);
    }
    catch (error) { toast.error(error instanceof Error ? error.message : "任务提交失败"); }
  };
  const runGroupVideo = async (request: { video_model: string; h3_mode: "auto" | "i2va" | "fl2va" }) => {
    try {
      const response = await generateVideo.mutateAsync({ groupId: group.id, model: request.video_model, mode: request.h3_mode, aspectRatio, revision: group.stages.video.revision, ...(group.video_plan ? { planRevision: group.video_plan.revision } : {}) });
      groupVideoTask.start({ scope: taskScope(response) });
      toast.success("H3 导演台组合视频已进入队列");
    } catch (error) { toast.error(error instanceof Error ? error.message : "组合视频任务提交失败"); }
  };
  const saveGroupVideoPlan = async (units: Array<{ beatIds: string[] }>) => {
    if (!group.video_plan || videoPlanSaving) return;
    setVideoPlanSaving(true);
    try {
      await updateNarrativeGroupVideoPlan(project, episode, {
        groupId: group.id,
        expectedRevision: group.video_plan.revision,
        units,
      });
      await groupsQuery.refetch();
      toast.success("视频方案已保存");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "视频方案保存失败");
    } finally {
      setVideoPlanSaving(false);
    }
  };
  const frames = groupFrameSummary(group.video_inputs ?? []);
  const changeVideoModel = async (videoModel: string) => {
    const previous = selectedVideoModelId;
    setSelectedVideoModelId(videoModel);
    try {
      await updateDefaults.mutateAsync({
        videoModel,
        videoMode: mediaDefaults?.h3_mode ?? "auto",
        narrativeSketchProvider: mediaDefaults?.narrative_sketch_provider,
        narrativeSketchModel: mediaDefaults?.narrative_sketch_model,
        narrativeRenderProvider: mediaDefaults?.narrative_render_provider,
        narrativeRenderModel: mediaDefaults?.narrative_render_model,
      });
      toast.success("项目默认视频模型已保存");
    } catch (error) {
      setSelectedVideoModelId(previous);
      toast.error(error instanceof Error ? error.message : "默认模型保存失败");
    }
  };
  return <div className="flex h-full min-h-0 overflow-hidden" data-narrative-group-workbench>
    <aside className="w-72 shrink-0 overflow-y-auto border-r border-white/10 p-4"><div className="mb-4 flex items-center justify-between"><h2 className="font-semibold">叙事组生产</h2><Button variant="ghost" size="icon" onClick={() => groupsQuery.refetch()}><RefreshCw className="size-4" /></Button></div><NarrativeGroupList groups={groups} selectedId={group.id} onSelect={(id) => select({ project, episode }, id)} /></aside>
    <GroupReferenceDialog open={pendingAction !== null} preview={referencesQuery.data?.ok ? referencesQuery.data.data : null} loading={referencesQuery.isLoading} error={referencesQuery.error instanceof Error ? referencesQuery.error : null} submitting={action.isPending} stage={pendingAction?.stage ?? "render"} defaultProvider={pendingAction?.stage === "sketch" ? mediaDefaults?.narrative_sketch_provider : mediaDefaults?.narrative_render_provider} defaultModel={pendingAction?.stage === "sketch" ? mediaDefaults?.narrative_sketch_model : mediaDefaults?.narrative_render_model} sketchReady={group.stages.sketch.status === "completed" && !!group.stages.sketch.grid_asset} onRetry={() => referencesQuery.refetch()} onSubmit={confirmAction} onOpenChange={(open) => { if (!open) setPendingAction(null); }} />
    <main className="min-w-0 flex-1 overflow-y-auto p-5"><div className="mb-5 flex flex-wrap items-start justify-between gap-3"><div><div className="text-xs text-primary">叙事组 {String(group.ordinal).padStart(2, "0")}</div><h1 className="mt-1 text-xl font-semibold">{group.title || `Beat ${group.beat_ids.join("–")}`}</h1><p className="mt-1 text-xs text-muted-foreground">{group.layout.rows}×{group.layout.columns} · {group.beat_ids.length} 个 Beat · 多宫格生成后服务端自动切分</p></div><div className="flex flex-wrap items-start justify-end gap-3">{aspectSelector}<ProjectVideoModelSelect value={modelId} models={models} saving={updateDefaults.isPending} onChange={changeVideoModel} /></div></div><GroupPipeline project={project} episode={episode} group={group} onAction={runAction} onRepairBeat={onRepairBeat} /><div className="mt-4"><GroupVideoStage modelId={modelId} mode={mediaDefaults?.h3_mode ?? model?.default_mode ?? "auto"} hasFirstFrame={frames.allHaveFirst} hasLastFrame={frames.allHaveLast} inputs={group.video_inputs} plan={group.video_plan} planSaving={videoPlanSaving} taskStatus={group.stages.video.status} available={model?.available ?? false} unavailableReason={model?.unavailable_reason} onPlanSave={saveGroupVideoPlan} onGenerate={runGroupVideo} /><GroupVideoResult stage={group.stages.video} onDialogueSourceChange={async ({ spanIndex, dialogueSource }) => {
      try { await updateDialogueSource.mutateAsync({ groupId: group.id, spanIndex, dialogueSource, revision: group.stages.video.revision }); toast.success("已提交重新合成，对应 H3 视频不会重新生成"); }
      catch (error) { toast.error(error instanceof Error ? error.message : "对白源切换提交失败"); }
    }} /></div></main>
  </div>;
}
