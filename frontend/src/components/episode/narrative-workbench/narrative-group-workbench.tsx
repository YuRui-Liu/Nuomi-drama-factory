// SPDX-License-Identifier: Elastic-2.0
import { Loader2, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { narrativeGroupTaskScope, narrativeGroupVideoTaskScope, updateNarrativeGroupVideoPlan, updateNarrativeGroupVideoSettings, useChangeNarrativeGroupStyle, useGenerateNarrativeGroupVideo, useGenerateNarrativeGroupVideoSegment, useNarrativeGroupAction, useNarrativeGroupReferences, useNarrativeGroups, useUpdateNarrativeGroupVideoDialogueSource, type NarrativeGridStage, type NarrativeGroupGenerationSelection } from "@/lib/queries/narrative-groups";
import { availableVideoModels, resolveVideoModel, resolveVideoMode, useMediaDefaults, useUpdateMediaDefaults, useVideoModels } from "@/lib/queries/media-models";
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
import { GroupVideoParameters } from "./group-video-parameters";
import { coerceNarrativeImageSize, supportedNarrativeImageSizes, type NarrativeImageSize } from "@/lib/narrative-image-resolution";
import { useStyles } from "@/lib/queries/styles";
import { GenerationBatchSummary } from "./group-grid-stage";
import { StyleChangeDialog, type StyleChangeDecision } from "./style-change-dialog";
import { GroupVideoSegmentList } from "./group-video-segment-list";

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
  const [videoSettingsSaving, setVideoSettingsSaving] = useState(false);
  const [selectedVideoModelId, setSelectedVideoModelId] = useState<string | null>(null);
  const [renderSettingsOpen, setRenderSettingsOpen] = useState(false);
  const [styleDialogOpen, setStyleDialogOpen] = useState(false);
  const [renderModelDraft, setRenderModelDraft] = useState("gpt-image-2");
  const [renderImageSizeDraft, setRenderImageSizeDraft] = useState<NarrativeImageSize>("1K");
  const generateVideo = useGenerateNarrativeGroupVideo(project, episode);
  const generateVideoSegment = useGenerateNarrativeGroupVideoSegment(project, episode);
  const changeGroupStyle = useChangeNarrativeGroupStyle(project, episode);
  const stylesQuery = useStyles(project);
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
  const videoMode = model
    ? resolveVideoMode(mediaDefaults?.h3_mode ?? model.default_mode, model)
    : "auto";
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
          videoMode,
          narrativeSketchProvider: pendingAction.stage === "sketch" ? (selection.providerId ?? "grsai-main") : mediaDefaults?.narrative_sketch_provider,
          narrativeSketchModel: pendingAction.stage === "sketch" ? (selection.model ?? "nano-banana-2") : mediaDefaults?.narrative_sketch_model,
          narrativeRenderProvider: pendingAction.stage === "render" ? (selection.providerId ?? "grsai-main") : mediaDefaults?.narrative_render_provider,
          narrativeRenderModel: pendingAction.stage === "render" ? (selection.model ?? "gpt-image-2") : mediaDefaults?.narrative_render_model,
          narrativeRenderImageSize: pendingAction.stage === "render" ? selection.imageSize : mediaDefaults?.narrative_render_image_size,
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
      const response = await generateVideo.mutateAsync({ groupId: group.id, model: request.video_model, mode: request.h3_mode, aspectRatio, revision: group.stages.video.revision, ...(group.video_plan ? { planRevision: group.video_plan.revision } : {}), ...(group.video_settings ? { settingsRevision: group.video_settings.revision } : {}) });
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
  const retryVideoSegment = async (segmentId: string) => {
    try {
      await generateVideoSegment.mutateAsync({ groupId: group.id, segmentId });
      toast.success(`片段 ${segmentId} 已重新进入队列`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "片段重试失败");
    }
  };
  const applyStyleChange = async (decision: StyleChangeDecision) => {
    try {
      await changeGroupStyle.mutateAsync({
        groupId: group.id,
        styleId: decision.styleId,
        action: decision.action,
      });
      setStyleDialogOpen(false);
      toast.success(decision.action === "redirect" ? "已创建重新导演任务" : "已更新叙事组画风");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "风格修改失败");
    }
  };
  const frames = groupFrameSummary(group.video_inputs ?? []);
  const videoOverrides = group.video_settings?.workflow_id === modelId
    ? group.video_settings.overrides
    : {};
  const projectVideoDefaults = mediaDefaults?.video_workflow_parameters?.[modelId] ?? {};
  const effectiveVideoResolution = videoOverrides.resolution
    ?? projectVideoDefaults.resolution
    ?? model?.parameters?.find((parameter) => parameter.key === "resolution")?.default;
  const needsVideoRegeneration = Boolean(
    group.stages.video.video_asset
    && group.stages.video.workflow_parameters?.resolution
    && group.stages.video.workflow_parameters.resolution !== effectiveVideoResolution,
  );
  const saveVideoOverride = async (key: string, value: string) => {
    setVideoSettingsSaving(true);
    try {
      await updateNarrativeGroupVideoSettings(project, episode, {
        groupId: group.id,
        expectedRevision: group.video_settings?.revision ?? 0,
        workflowId: modelId,
        overrides: { ...videoOverrides, [key]: value },
      });
      await groupsQuery.refetch();
      toast.success("本组视频清晰度已保存");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "视频清晰度保存失败");
    } finally {
      setVideoSettingsSaving(false);
    }
  };
  const restoreVideoDefault = async (key: string) => {
    const overrides = { ...videoOverrides };
    delete overrides[key];
    setVideoSettingsSaving(true);
    try {
      await updateNarrativeGroupVideoSettings(project, episode, {
        groupId: group.id,
        expectedRevision: group.video_settings?.revision ?? 0,
        workflowId: modelId,
        overrides,
      });
      await groupsQuery.refetch();
      toast.success("已恢复项目默认清晰度");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "恢复项目默认失败");
    } finally {
      setVideoSettingsSaving(false);
    }
  };
  const promoteVideoDefault = async (key: string, value: string) => {
    setVideoSettingsSaving(true);
    try {
      await updateDefaults.mutateAsync({
        videoModel: modelId,
        videoMode,
        videoWorkflowParameters: {
          ...(mediaDefaults?.video_workflow_parameters ?? {}),
          [modelId]: { ...projectVideoDefaults, [key]: value },
        },
      });
      const overrides = { ...videoOverrides };
      delete overrides[key];
      await updateNarrativeGroupVideoSettings(project, episode, {
        groupId: group.id,
        expectedRevision: group.video_settings?.revision ?? 0,
        workflowId: modelId,
        overrides,
      });
      await groupsQuery.refetch();
      toast.success("已设为项目默认清晰度");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "项目默认清晰度保存失败");
    } finally {
      setVideoSettingsSaving(false);
    }
  };
  const changeVideoModel = async (videoModel: string) => {
    const targetModel = models.find((item) => item.id === videoModel);
    if (!targetModel) return;
    const targetMode = resolveVideoMode(mediaDefaults?.h3_mode ?? targetModel.default_mode, targetModel);
    const previous = selectedVideoModelId;
    setSelectedVideoModelId(videoModel);
    try {
      await updateDefaults.mutateAsync({
        videoModel,
        videoMode: targetMode,
        narrativeSketchProvider: mediaDefaults?.narrative_sketch_provider,
        narrativeSketchModel: mediaDefaults?.narrative_sketch_model,
        narrativeRenderProvider: mediaDefaults?.narrative_render_provider,
        narrativeRenderModel: mediaDefaults?.narrative_render_model,
        narrativeRenderImageSize: mediaDefaults?.narrative_render_image_size,
      });
      toast.success("项目默认视频模型已保存");
    } catch (error) {
      setSelectedVideoModelId(previous);
      toast.error(error instanceof Error ? error.message : "默认模型保存失败");
    }
  };
  const openRenderSettings = () => {
    const savedModel = mediaDefaults?.narrative_render_model ?? "gpt-image-2";
    setRenderModelDraft(savedModel);
    setRenderImageSizeDraft(coerceNarrativeImageSize(savedModel, mediaDefaults?.narrative_render_image_size));
    setRenderSettingsOpen(true);
  };
  const saveRenderSettings = async () => {
    try {
      await updateDefaults.mutateAsync({
        videoModel: modelId,
        videoMode,
        narrativeSketchProvider: mediaDefaults?.narrative_sketch_provider,
        narrativeSketchModel: mediaDefaults?.narrative_sketch_model,
        narrativeRenderProvider: mediaDefaults?.narrative_render_provider,
        narrativeRenderModel: renderModelDraft,
        narrativeRenderImageSize: renderImageSizeDraft,
      });
      setRenderSettingsOpen(false);
      toast.success("项目实图设置已保存");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "实图设置保存失败");
    }
  };
  return <div className="flex h-full min-h-0 overflow-hidden" data-narrative-group-workbench>
    <aside className="w-72 shrink-0 overflow-y-auto border-r border-white/10 p-4"><div className="mb-4 flex items-center justify-between"><h2 className="font-semibold">叙事组生产</h2><Button variant="ghost" size="icon" onClick={() => groupsQuery.refetch()}><RefreshCw className="size-4" /></Button></div><NarrativeGroupList groups={groups} selectedId={group.id} onSelect={(id) => select({ project, episode }, id)} /></aside>
    <StyleChangeDialog open={styleDialogOpen} currentStyleId={group.effective_style_snapshot?.style_id ?? null} availableStyles={(stylesQuery.data?.data ?? []).map((style) => ({ id: style.id, label: style.label || style.name }))} saving={changeGroupStyle.isPending} onOpenChange={setStyleDialogOpen} onApply={applyStyleChange} />
    <GroupReferenceDialog open={pendingAction !== null} preview={referencesQuery.data?.ok ? referencesQuery.data.data : null} loading={referencesQuery.isLoading} error={referencesQuery.error instanceof Error ? referencesQuery.error : null} submitting={action.isPending} stage={pendingAction?.stage ?? "render"} defaultProvider={pendingAction?.stage === "sketch" ? mediaDefaults?.narrative_sketch_provider : mediaDefaults?.narrative_render_provider} defaultModel={pendingAction?.stage === "sketch" ? mediaDefaults?.narrative_sketch_model : mediaDefaults?.narrative_render_model} defaultImageSize={mediaDefaults?.narrative_render_image_size} sketchReady={group.stages.sketch.status === "completed" && !!group.stages.sketch.grid_asset} onRetry={() => referencesQuery.refetch()} onSubmit={confirmAction} onOpenChange={(open) => { if (!open) setPendingAction(null); }} />
    <main className="min-w-0 flex-1 overflow-y-auto p-5"><div className="mb-5 flex flex-wrap items-start justify-between gap-3"><div><div className="text-xs text-primary">叙事组 {String(group.ordinal).padStart(2, "0")}</div><h1 className="mt-1 text-xl font-semibold">{group.title || `Beat ${group.beat_ids.join("–")}`}</h1><p className="mt-1 text-xs text-muted-foreground">{group.layout.rows}×{group.layout.columns} · {group.beat_ids.length} 个 Beat · 多宫格生成后服务端自动切分</p>{group.stages.render.requested_image_size || group.stages.render.actual_pixel_size ? <p className="mt-1 text-[11px] text-muted-foreground">请求 {group.stages.render.requested_image_size ?? "—"}{group.stages.render.requested_pixel_size ? ` / ${group.stages.render.requested_pixel_size}` : ""} · 实际 {group.stages.render.actual_pixel_size ?? "—"}</p> : null}</div><div className="flex flex-wrap items-start justify-end gap-3">{aspectSelector}<Button variant="outline" size="sm" onClick={openRenderSettings}>实图设置</Button><Button variant="outline" size="sm" onClick={() => setStyleDialogOpen(true)}>修改风格</Button><ProjectVideoModelSelect value={modelId} models={models} saving={updateDefaults.isPending} onChange={changeVideoModel} /></div></div>{renderSettingsOpen ? <section className="mb-4 flex flex-wrap items-end gap-3 rounded-lg border border-white/10 bg-black/20 p-3"><label className="space-y-1 text-xs"><span className="block text-muted-foreground">项目实图模型</span><select aria-label="项目实图模型" className="h-9 rounded-md border border-input bg-background px-3" value={renderModelDraft} onChange={(event) => { const nextModel = event.target.value; setRenderModelDraft(nextModel); setRenderImageSizeDraft(coerceNarrativeImageSize(nextModel, renderImageSizeDraft)); }}><option value="gpt-image-2">gpt-image-2</option><option value="gpt-image-2-vip">gpt-image-2-vip</option></select></label><label className="space-y-1 text-xs"><span className="block text-muted-foreground">项目实图分辨率</span><select aria-label="项目实图分辨率" className="h-9 rounded-md border border-input bg-background px-3" value={renderImageSizeDraft} onChange={(event) => setRenderImageSizeDraft(event.target.value as NarrativeImageSize)}>{supportedNarrativeImageSizes(renderModelDraft).map((size) => <option key={size} value={size}>{size}</option>)}</select></label><Button size="sm" disabled={updateDefaults.isPending} onClick={saveRenderSettings}>保存实图设置</Button><Button variant="ghost" size="sm" onClick={() => setRenderSettingsOpen(false)}>取消</Button></section> : null}<GenerationBatchSummary batches={group.generation_batches ?? []} /><GroupPipeline project={project} episode={episode} group={group} onAction={runAction} onRepairBeat={onRepairBeat} /><div className="mt-4 space-y-3"><GroupVideoParameters parameters={model?.parameters ?? []} projectDefaults={projectVideoDefaults} overrides={videoOverrides} aspectRatio={aspectRatio} busy={group.stages.video.status === "queued" || group.stages.video.status === "running"} saving={videoSettingsSaving || updateDefaults.isPending} onSaveOverride={saveVideoOverride} onRestoreDefault={restoreVideoDefault} onPromoteDefault={promoteVideoDefault} />{needsVideoRegeneration ? <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">清晰度设置已变化，当前旧视频仍可预览；点击生成组合视频后才会按新设置重新生成。</p> : null}<GroupVideoStage modelId={modelId} mode={videoMode} hasFirstFrame={frames.allHaveFirst} hasLastFrame={frames.allHaveLast} inputs={group.video_inputs} plan={group.video_plan} planSaving={videoPlanSaving} taskStatus={group.stages.video.status} available={model?.available ?? false} unavailableReason={model?.unavailable_reason} onPlanSave={saveGroupVideoPlan} onGenerate={runGroupVideo} /><GroupVideoSegmentList segments={group.video_segments ?? []} onRetrySegment={retryVideoSegment} /><GroupVideoResult project={project} episode={episode} groupId={group.id} stage={group.stages.video} onDialogueSourceChange={async ({ spanIndex, dialogueSource }) => {
      try { await updateDialogueSource.mutateAsync({ groupId: group.id, spanIndex, dialogueSource, revision: group.stages.video.revision }); toast.success("已提交重新合成，对应 H3 视频不会重新生成"); }
      catch (error) { toast.error(error instanceof Error ? error.message : "对白源切换提交失败"); }
    }} /></div></main>
  </div>;
}
