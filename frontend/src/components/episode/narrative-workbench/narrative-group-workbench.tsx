// SPDX-License-Identifier: Elastic-2.0
import { Loader2, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "@tanstack/react-router";
import { HTTPError } from "ky";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { narrativeGroupTaskScope, narrativeGroupVideoTaskScope, updateNarrativeGroupVideoPlan, updateNarrativeGroupVideoSettings, useChangeNarrativeGroupStyle, useGenerateNarrativeGroupVideo, useGenerateNarrativeGroupVideoSegment, useNarrativeGroupAction, useNarrativeGroupReferences, useNarrativeGroups, useNarrativeGroupVideoPrompts, useNarrativeGroupVideoReferencePreview, useUpdateNarrativeGroupVideoDialogueSource, useUploadNarrativeReference, type NarrativeGridStage, type PlannedNarrativeGroupGenerationSelection, type VideoReferencePreview } from "@/lib/queries/narrative-groups";
import { availableVideoModels, h3ModeAvailability, resolveVideoModel, resolveVideoMode, useMediaDefaults, useUpdateMediaDefaults, useVideoModels, type H3VideoMode } from "@/lib/queries/media-models";
import { episodeWorkbenchScopeKey, useEpisodeWorkbenchStore } from "@/stores/episode-workbench-store";
import { useTaskController } from "@/hooks/use-task-controller";
import { queryKeys } from "@/lib/query-keys";
import { aspectRatioForOrientation, type Orientation } from "@/lib/aspect-ratio";
import { useUpdateProject } from "@/lib/queries/projects";
import { NarrativeGroupList, narrativeGroupDisplayTitle, narrativeGroupScopeLabel } from "./narrative-group-list";
import { GroupPipeline } from "./group-pipeline";
import { GroupVideoStage, groupFrameSummary } from "./group-video-stage";
import { GroupVideoResult } from "./group-video-result";
import { GroupReferenceDialog } from "./group-reference-dialog";
import { useProjectAspectRatio } from "@/stores/aspect-ratio-store";
import { NarrativeAspectSelector } from "./narrative-aspect-selector";
import { GroupVideoParameters } from "./group-video-parameters";
import { coerceNarrativeImageSize, supportedNarrativeImageSizes, type NarrativeImageSize } from "@/lib/narrative-image-resolution";
import { useStyles } from "@/lib/queries/styles";
import { GenerationBatchSummary } from "./group-grid-stage";
import { StyleChangeDialog, type StyleChangeDecision } from "./style-change-dialog";
import { GroupVideoSegmentList } from "./group-video-segment-list";
import { GroupVideoReferenceDialog } from "./group-video-reference-dialog";

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

function validVideoReferences(
  preview: VideoReferencePreview | null,
  policy: { min_images: number; max_images: number } | undefined,
) {
  if (!preview || !policy) return false;
  const selected = preview.selected;
  return selected.length >= policy.min_images
    && selected.length <= policy.max_images
    && new Set(selected.map((item) => item.reference_id)).size === selected.length
    && selected.every((item) => {
      const value = item.subject_description.trim();
      return value.length > 0 && value.length <= 500 && !/\r|\n/.test(value);
    });
}

export function NarrativeGroupWorkbench({ project, episode, onRepairBeat, view = "all", onReviewRequest }: { project: string; episode: number; onRepairBeat?: (beatId: string) => void; view?: "image" | "video" | "all"; onReviewRequest?: () => void }) {
  const navigate = useNavigate();
  const { t } = useTranslation();
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
  const [videoControlsTarget, setVideoControlsTarget] = useState<HTMLDivElement | null>(null);
  const [styleDialogOpen, setStyleDialogOpen] = useState(false);
  const [renderModelDraft, setRenderModelDraft] = useState("gpt-image-2");
  const [renderImageSizeDraft, setRenderImageSizeDraft] = useState<NarrativeImageSize>("1K");
  const [videoReferenceDialogOpen, setVideoReferenceDialogOpen] = useState(false);
  const [videoReferenceDirty, setVideoReferenceDirty] = useState(false);
  const [savedReferencePreview, setSavedReferencePreview] = useState<{ groupId: string; preview: VideoReferencePreview } | null>(null);
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
  const requestedGroup = new URLSearchParams(window.location.search).get('group');
  const appliedGroupLink = useRef<string | null>(null);
  useEffect(() => {
    const linkKey = JSON.stringify([project, episode, requestedGroup]);
    if (requestedGroup && appliedGroupLink.current !== linkKey && groups.some(item => item.id === requestedGroup)) {
      appliedGroupLink.current = linkKey;
      select({ project, episode }, requestedGroup);
    }
    if (!requestedGroup) appliedGroupLink.current = null;
  }, [project, episode, requestedGroup, groups, select]);
  const group = groups.find((item) => item.id === selectedId) ?? groups.find((item) => item.stages.video.status !== "completed") ?? groups[0];
  const referencesQuery = useNarrativeGroupReferences(project, episode, pendingAction?.groupId ?? "", pendingAction?.stage ?? "render", pendingAction !== null);
  const referenceUpload = useUploadNarrativeReference(project, episode, pendingAction?.groupId ?? "", pendingAction?.stage ?? "render");
  const catalog = modelsQuery.data?.ok ? modelsQuery.data.data : [];
  const models = availableVideoModels(catalog);
  const model = resolveVideoModel(selectedVideoModelId ?? mediaDefaults?.video_model, catalog);
  const modelId = model?.id ?? "";
  const videoMode = model
    ? resolveVideoMode(mediaDefaults?.h3_mode ?? model.default_mode, model)
    : "auto";
  const referencePolicy = model?.reference_policy?.required ? model.reference_policy : undefined;
  const videoReferenceQuery = useNarrativeGroupVideoReferencePreview(
    project, episode, group?.id ?? "", Boolean(referencePolicy && group?.id),
  );
  const generatedVideoPromptQuery = useNarrativeGroupVideoPrompts(
    project, episode, group?.id ?? "",
    Boolean(referencePolicy && group?.stages.video.manifest_asset),
  );
  const queriedReferencePreview = videoReferenceQuery.data?.ok ? videoReferenceQuery.data.data : null;
  useEffect(() => {
    if (group && queriedReferencePreview) setSavedReferencePreview({ groupId: group.id, preview: queriedReferencePreview });
  }, [group?.id, queriedReferencePreview]);
  const currentReferencePreview = savedReferencePreview && savedReferencePreview.groupId === group?.id
    ? savedReferencePreview.preview : queriedReferencePreview;
  const frames = groupFrameSummary(group?.video_inputs ?? []);
  const selectedModeAvailability = model ? h3ModeAvailability(model, {
    hasFirstFrame: frames.allHaveFirst,
    hasLastFrame: frames.allHaveLast,
    referenceCount: currentReferencePreview?.selected.length ?? 0,
  }, videoMode) : undefined;
  const referenceRevisionSynchronized = !group?.video_reference_settings
    || currentReferencePreview?.revision === group.video_reference_settings.revision;
  useEffect(() => {
    if (referencePolicy && group?.video_reference_settings && queriedReferencePreview
      && queriedReferencePreview.revision !== group.video_reference_settings.revision) {
      void videoReferenceQuery.refetch();
    }
  }, [referencePolicy, group?.video_reference_settings?.revision, queriedReferencePreview?.revision]);
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
  useEffect(() => { setPendingAction(null); setVideoReferenceDialogOpen(false); setVideoReferenceDirty(false); }, [project, episode, group?.id]);
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
      toast.success("画幅已变更，请重新生成并激活镜头方案", {
        duration: 10000,
        action: { label: "前往镜头方案", onClick: () => void navigate({
          to: "/projects/$project/episodes/$episode/script", params: { project, episode: String(episode) },
        }) },
      });
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
  const confirmAction = async (selection: PlannedNarrativeGroupGenerationSelection) => {
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
    catch (error) {
      if (error instanceof HTTPError) {
        const body = await error.response.clone().json().catch(() => null) as { detail?: { code?: string; message?: string } } | null;
        if (body?.detail?.code === "STALE_REFERENCE_BINDING") {
          await referencesQuery.refetch();
          toast.error(body.detail.message || "规划引用已更新，请确认最新选择后重试");
          return;
        }
        if (body?.detail?.message) {
          toast.error(body.detail.message);
          return;
        }
      }
      toast.error(error instanceof Error ? error.message : "任务提交失败");
    }
  };
  const runGroupVideo = async (request: { video_model: string; h3_mode: H3VideoMode }) => {
    if (request.video_model !== modelId) return;
    if (!selectedModeAvailability?.available || videoPlanSaving || videoSettingsSaving
      || updateDefaults.isPending || (group.video_settings?.workflow_id && group.video_settings.workflow_id !== modelId)
      || group.stages.video.status === "queued" || group.stages.video.status === "running") return;
    if (referencePolicy && (
      videoReferenceQuery.isFetching
      || videoReferenceQuery.isError
      || videoReferenceQuery.data?.ok !== true
      || videoReferenceDirty
      || !referenceRevisionSynchronized
      || !validVideoReferences(currentReferencePreview, {...referencePolicy, max_images: currentReferencePreview?.max_images ?? referencePolicy.max_images})
    )) return;
    try {
      const response = await generateVideo.mutateAsync({ groupId: group.id, model: request.video_model, mode: request.h3_mode, aspectRatio, revision: group.stages.video.revision, ...(group.video_plan ? { planRevision: group.video_plan.revision } : {}), ...(group.video_settings ? { settingsRevision: group.video_settings.revision } : {}), ...(referencePolicy && currentReferencePreview ? { referenceRevision: currentReferencePreview.revision } : {}) });
      groupVideoTask.start({ scope: taskScope(response) });
      toast.success("H3 导演台组合视频已进入队列");
    } catch (error) { toast.error(error instanceof Error ? error.message : "组合视频任务提交失败"); }
  };
  const saveGroupVideoPlan = async (units: Array<{ beatIds: string[]; mode?: "i2va" | "fl2va" }>) => {
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
    if (!selectedModeAvailability?.available || videoPlanSaving || videoSettingsSaving
      || updateDefaults.isPending || (group.video_settings?.workflow_id && group.video_settings.workflow_id !== modelId)
      || group.stages.video.status === "queued" || group.stages.video.status === "running") return;
    if (referencePolicy && (videoReferenceQuery.isFetching || videoReferenceQuery.isError
      || videoReferenceQuery.data?.ok !== true || videoReferenceDirty || !referenceRevisionSynchronized
      || !validVideoReferences(currentReferencePreview, {...referencePolicy, max_images: currentReferencePreview?.max_images ?? referencePolicy.max_images}))) return;
    try {
      await generateVideoSegment.mutateAsync({ groupId: group.id, segmentId, model: modelId, mode: videoMode, aspectRatio, revision: group.stages.video.revision, ...(group.video_plan ? { planRevision: group.video_plan.revision } : {}), ...(group.video_settings ? { settingsRevision: group.video_settings.revision } : {}), ...(referencePolicy && currentReferencePreview ? { referenceRevision: currentReferencePreview.revision } : {}) });
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
  const generatedReferenceRevision = generatedVideoPromptQuery.data?.ok
    ? generatedVideoPromptQuery.data.data.reference_settings_revision : null;
  const backendReportsStaleReferences = Boolean(
    referencePolicy
    && group.stages.video.needs_regeneration
    && group.stages.video.stale_reason === "video_reference_settings_changed"
  );
  const referenceNeedsVideoRegeneration = Boolean(
    backendReportsStaleReferences
    || (referencePolicy
      && group.stages.video.manifest_asset
      && !generatedVideoPromptQuery.isLoading
      && !generatedVideoPromptQuery.isFetching
      && !generatedVideoPromptQuery.isError
      && generatedVideoPromptQuery.data?.ok === true
      && typeof generatedReferenceRevision === "number"
      && group.video_reference_settings
      && generatedReferenceRevision !== group.video_reference_settings.revision)
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
    setVideoSettingsSaving(true);
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
      if (group.video_settings?.workflow_id !== videoModel) {
        await updateNarrativeGroupVideoSettings(project, episode, {
          groupId: group.id,
          expectedRevision: group.video_settings?.revision ?? 0,
          workflowId: videoModel,
          overrides: {},
        });
        await groupsQuery.refetch();
      }
      toast.success("项目默认视频模型已保存");
    } catch (error) {
      setSelectedVideoModelId(previous);
      toast.error(error instanceof Error ? error.message : "默认模型保存失败");
    } finally {
      setVideoSettingsSaving(false);
    }
  };
  const changeVideoMode = async (nextMode: H3VideoMode) => {
    if (!model || nextMode === videoMode) return;
    try {
      await updateDefaults.mutateAsync({ videoModel: modelId, videoMode: nextMode });
      toast.success("项目默认 H3 模式已保存");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "H3 模式保存失败");
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
  const referencePreview = videoReferenceQuery.data?.ok === true && queriedReferencePreview
    ? (savedReferencePreview?.groupId === group.id ? savedReferencePreview.preview : queriedReferencePreview)
    : null;
  // A failed or still-loading preview must not read as "no references selected".
  // The stored per-group selection is what the next run will use, and a bogus 0
  // here silently flipped auto mode to I2VA on a workflow whose reference policy
  // requires reference images (`resolveAutomaticH3Mode`: references -> ref2va).
  // The generate gate below still requires a loaded, valid preview.
  const selectedReferences = referencePreview?.selected
    ?? group.video_reference_settings?.references
    ?? [];
  const referenceMaxImages = referencePreview?.max_images ?? referencePolicy?.max_images ?? 0;
  const referenceValid = referenceRevisionSynchronized && validVideoReferences(
    referencePreview,
    referencePolicy ? {...referencePolicy, max_images: referenceMaxImages} : undefined,
  );
  const videoWorkflowSynchronized = !group.video_settings || group.video_settings.workflow_id === modelId;
  const videoRequestAllowed = Boolean(selectedModeAvailability?.available && !videoPlanSaving
    && !videoSettingsSaving && !updateDefaults.isPending && videoWorkflowSynchronized
    && group.stages.video.status !== "queued" && group.stages.video.status !== "running"
    && (!referencePolicy || (referenceValid && !videoReferenceDirty && !videoReferenceQuery.isFetching
      && !videoReferenceQuery.isError && videoReferenceQuery.data?.ok === true)));
  return <div className="grid h-full min-h-0 grid-cols-1 overflow-y-auto lg:grid-cols-[188px_minmax(0,1fr)_280px] lg:overflow-hidden" data-narrative-group-workbench>
    <aside className="max-h-48 min-h-0 overflow-y-auto border-b border-white/10 bg-white/[0.015] p-3 lg:max-h-none lg:border-r lg:border-b-0">
      <div className="mb-3 flex items-center justify-between"><h2 className="text-sm font-semibold">叙事组</h2><Button variant="ghost" size="icon-sm" aria-label="刷新叙事组" onClick={() => groupsQuery.refetch()}><RefreshCw className="size-3.5" /></Button></div>
      <NarrativeGroupList groups={groups} selectedId={group.id} onSelect={(id) => select({ project, episode }, id)} />
    </aside>
    <StyleChangeDialog open={styleDialogOpen} currentStyleId={group.effective_style_snapshot?.style_id ?? null} availableStyles={(stylesQuery.data?.data ?? []).map((style) => ({ id: style.id, label: style.label || style.name }))} saving={changeGroupStyle.isPending} onOpenChange={setStyleDialogOpen} onApply={applyStyleChange} />
    <GroupReferenceDialog project={project} episode={episode} groupId={pendingAction?.groupId ?? ""} open={pendingAction !== null} preview={referencesQuery.data?.ok ? referencesQuery.data.data : null} loading={referencesQuery.isLoading} error={referencesQuery.error instanceof Error ? referencesQuery.error : null} submitting={action.isPending} stage={pendingAction?.stage ?? "render"} defaultProvider={pendingAction?.stage === "sketch" ? mediaDefaults?.narrative_sketch_provider : mediaDefaults?.narrative_render_provider} defaultModel={pendingAction?.stage === "sketch" ? mediaDefaults?.narrative_sketch_model : mediaDefaults?.narrative_render_model} defaultImageSize={mediaDefaults?.narrative_render_image_size} sketchReady={group.stages.sketch.status === "completed" && !!group.stages.sketch.grid_asset} uploadingReference={referenceUpload.isPending} onResetUploadError={referenceUpload.reset} onUploadReference={async (file) => { const response = await referenceUpload.mutateAsync({ file }); return response.ok ? response.data : null; }} onResolvePlanning={() => { setPendingAction(null); void navigate({ to: "/projects/$project/episodes/$episode/script", params: { project, episode: String(episode) } }); }} onRetry={() => referencesQuery.refetch()} onSubmit={confirmAction} onOpenChange={(open) => { if (!open) setPendingAction(null); }} />
    {referencePolicy ? <GroupVideoReferenceDialog open={videoReferenceDialogOpen} onOpenChange={(open) => { setVideoReferenceDialogOpen(open); if (!open) setVideoReferenceDirty(false); }} project={project} episode={episode} groupId={group.id} preview={referencePreview} loading={videoReferenceQuery.isFetching} error={videoReferenceQuery.error instanceof Error ? videoReferenceQuery.error : videoReferenceQuery.data?.ok === false ? new Error(videoReferenceQuery.data.error) : null} minImages={referencePolicy.min_images} maxImages={referenceMaxImages} onDirtyChange={setVideoReferenceDirty} onRefresh={async () => { const response = await videoReferenceQuery.refetch(); return response.data?.ok ? response.data.data : undefined; }} onSaved={(saved) => { setSavedReferencePreview({ groupId: group.id, preview: saved }); setVideoReferenceDirty(false); void groupsQuery.refetch(); }} /> : null}

    <main className="min-h-0 min-w-0 overflow-y-auto p-4">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0"><p className="text-[11px] text-primary">叙事组 {String(group.ordinal).padStart(2, "0")} · {narrativeGroupScopeLabel(group)}</p><h1 className="mt-1 text-base font-semibold">{narrativeGroupDisplayTitle(group)}</h1></div>
        {onReviewRequest && <Button size="sm" variant="ghost" onClick={onReviewRequest}>审核镜头方案</Button>}
      </header>
      <section hidden={view === "video"} aria-label="分镜生图工作区">
        {!!group.generation_batches?.length && <details className="mb-3 text-xs text-muted-foreground"><summary className="cursor-pointer">{group.generation_batches.length} 个生成批次 · 展开查看模型与分辨率</summary><GenerationBatchSummary batches={group.generation_batches} /></details>}
        <GroupPipeline project={project} episode={episode} group={group} onAction={runAction} onRepairBeat={onRepairBeat} onRegenerateStage={(stage) => runAction(stage, "regenerate")} />
      </section>
      <section hidden={view === "image"} aria-label="视频制作工作区" className="space-y-4">
        <section aria-label="当前组视频结果" className="mb-4"><GroupVideoResult project={project} episode={episode} groupId={group.id} stage={group.stages.video} onDialogueSourceChange={async ({ spanIndex, dialogueSource }) => {
      try {
        const response = await updateDialogueSource.mutateAsync({ groupId: group.id, spanIndex, dialogueSource, revision: group.stages.video.revision });
        toast.success(response.ok && response.data.recomposition_deferred
          ? "音频来源已保存，待本集视频与所选音轨齐全后可合成"
          : dialogueSource === "external_tts"
            ? "已提交角色声音克隆，将自动准备配音与环境音轨后合成，无需重新生成视频"
            : "已提交重新合成，对应 H3 视频不会重新生成");
      }
      catch (error) { toast.error(error instanceof Error ? error.message : "对白源切换提交失败"); }
    }} /></section>
        {referenceNeedsVideoRegeneration ? <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">{t("narrativeVideoReferences.staleVideo")}</p> : needsVideoRegeneration ? <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">清晰度设置已变化，旧视频仍保留。确认参数后重新生成。</p> : null}
        <GroupVideoStage controlsTarget={videoControlsTarget} modelId={modelId} mode={videoMode} models={catalog} modelSaving={updateDefaults.isPending || videoSettingsSaving} onModelChange={changeVideoModel} onModeChange={changeVideoMode} hasFirstFrame={frames.allHaveFirst} hasLastFrame={frames.allHaveLast} inputs={group.video_inputs} plan={group.video_plan} planSaving={videoPlanSaving || videoSettingsSaving || updateDefaults.isPending} workflowSynchronized={videoWorkflowSynchronized} taskStatus={group.stages.video.status} available={model?.available ?? false} unavailableReason={model?.unavailable_reason} reference={referencePolicy ? { required: true, count: selectedReferences.length, max: referenceMaxImages, valid: referenceValid, loading: videoReferenceQuery.isFetching, error: videoReferenceQuery.isError || videoReferenceQuery.data?.ok === false, dirty: videoReferenceDirty, onManage: () => setVideoReferenceDialogOpen(true) } : undefined} onPlanSave={saveGroupVideoPlan} onGenerate={runGroupVideo} />
        <GroupVideoSegmentList segments={group.video_segments ?? []} onRetrySegment={videoRequestAllowed ? retryVideoSegment : undefined} />
      </section>
    </main>
    <aside className="min-h-0 min-w-0 overflow-y-auto border-t border-white/10 bg-white/[0.025] p-4 lg:border-t-0 lg:border-l">
      <section hidden={view === "video"} aria-label="分镜生成参数" className="space-y-4">
        <h2 className="text-sm font-semibold">参考与生成参数</h2>
        {group.stages.render.requested_pixel_size && <p className="text-[11px] text-muted-foreground">请求 {group.stages.render.requested_image_size ?? "—"} / {group.stages.render.requested_pixel_size} · 实际 {group.stages.render.actual_pixel_size ?? "—"}</p>}
        <div className="border-b border-white/10 pb-4">{aspectSelector}</div>
        <dl className="space-y-3 text-xs"><div><dt className="text-muted-foreground">实图模型</dt><dd className="mt-1 break-all">{mediaDefaults?.narrative_render_model || "尚未配置"}</dd></div><div><dt className="text-muted-foreground">分辨率</dt><dd className="mt-1">{group.stages.render.requested_image_size ?? mediaDefaults?.narrative_render_image_size ?? "1K"}{group.stages.render.actual_pixel_size ? " · 实际 " + group.stages.render.actual_pixel_size : ""}</dd></div><div><dt className="text-muted-foreground">多宫格</dt><dd className="mt-1">{group.layout.rows} × {group.layout.columns}</dd></div></dl>
        <div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" onClick={openRenderSettings}>实图设置</Button><Button variant="outline" size="sm" onClick={() => setStyleDialogOpen(true)}>修改风格</Button></div>
        {renderSettingsOpen ? <section className="mb-4 flex flex-wrap items-end gap-3 rounded-lg border border-white/10 bg-black/20 p-3"><label className="space-y-1 text-xs"><span className="block text-muted-foreground">项目实图模型</span><select aria-label="项目实图模型" className="h-9 rounded-md border border-input bg-background px-3" value={renderModelDraft} onChange={(event) => { const nextModel = event.target.value; setRenderModelDraft(nextModel); setRenderImageSizeDraft(coerceNarrativeImageSize(nextModel, renderImageSizeDraft)); }}><option value="gpt-image-2">gpt-image-2</option><option value="gpt-image-2-vip">gpt-image-2-vip</option></select></label><label className="space-y-1 text-xs"><span className="block text-muted-foreground">项目实图分辨率</span><select aria-label="项目实图分辨率" className="h-9 rounded-md border border-input bg-background px-3" value={renderImageSizeDraft} onChange={(event) => setRenderImageSizeDraft(event.target.value as NarrativeImageSize)}>{supportedNarrativeImageSizes(renderModelDraft).map((size) => <option key={size} value={size}>{size}</option>)}</select></label><Button size="sm" disabled={updateDefaults.isPending} onClick={saveRenderSettings}>保存实图设置</Button><Button variant="ghost" size="sm" onClick={() => setRenderSettingsOpen(false)}>取消</Button></section> : null}
        <p className="text-xs leading-6 text-muted-foreground">生成前可核对人物、场景和道具参考。选用新版本后，既有图像与视频仍保留。</p>
        <Button className="w-full" onClick={() => runAction("render", group.stages.render.grid_asset ? "regenerate" : "generate")} disabled={action.isPending || ["queued","running"].includes(group.stages.render.status)}>检查参考并生成分镜</Button>
      </section>
      <section hidden={view === "image"} aria-label="视频生成参数" className="space-y-4">
        <h2 className="text-sm font-semibold">视频参考与参数</h2>
        <div ref={setVideoControlsTarget} className="[&>div]:!items-stretch [&>div]:!flex-col [&_button]:max-w-full [&_button]:whitespace-normal" />
        <GroupVideoParameters parameters={model?.parameters ?? []} projectDefaults={projectVideoDefaults} overrides={videoOverrides} aspectRatio={aspectRatio} busy={group.stages.video.status === "queued" || group.stages.video.status === "running"} saving={videoSettingsSaving || updateDefaults.isPending} onSaveOverride={saveVideoOverride} onRestoreDefault={restoreVideoDefault} onPromoteDefault={promoteVideoDefault} />
        <Button variant="outline" className="w-full" onClick={() => void navigate({to:"/projects/$project/episodes/$episode/compose",params:{project,episode:String(episode)}})}>前往整集合成 →</Button>
      </section>
    </aside>
  </div>;
}
