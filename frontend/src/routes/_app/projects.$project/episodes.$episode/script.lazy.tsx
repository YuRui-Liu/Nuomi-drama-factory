// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createLazyFileRoute, Link, useRouterState, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, Sparkles } from "lucide-react";

import {
  isPlanEpisodeAssetsResult,
  useEpisodeBeats,
  useEpisodeDetail,
  usePlanEpisodeProps,
  usePlanEpisodeScenes,
  usePlanIdentities,
  useUpdateEpisode,
} from "@/lib/queries/episodes";
import { useCharacters } from "@/lib/queries/characters";
import { useProject } from "@/lib/queries/projects";
import { useGenerateRewrite } from "@/lib/queries/scripts";
import { useDirectorPlans } from "@/lib/queries/director-plans";
import { AssetPlanningPrerequisite, assetPlanningBlockReason } from "@/components/episode/asset-planning-prerequisite";
import { AssetPlanningFailure, type PlanningAssetKind } from "@/components/episode/asset-planning-failure";
import { useTaskController } from "@/hooks/use-task-controller";
import { queryKeys } from "@/lib/query-keys";
import {
  backendErrorToastMessage,
  BillingRuleNotConfiguredError,
} from "@/lib/api-errors";
import { useGenerationCreditCost } from "@/lib/queries/generation-credit-cost";
import { TASK_TYPES } from "@/lib/task-types";
import { IdentityPickerDialog } from "@/components/identity-picker-dialog";
import {
  EpisodeAssetPlanning,
  type AssetPlanningCategory,
} from "@/components/episode/episode-asset-planning";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EpisodeSourceEditor } from "@/components/episode/episode-source-editor";
import { DirectorReviewWorkbench } from "@/components/episode/director-review";
import { ScriptBeatPreview } from "@/components/episode/script-beat-preview";
import { ScreenplayWorkbench } from "@/components/episode/screenplay-workbench";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { saveScopes, trackSave } from "@/stores/save-status-store";
import type { Character } from "@/types/character";

const REWRITE_TARGET_BEATS_MIN = 5;
const REWRITE_TARGET_BEATS_MAX = 80;
const REWRITE_BEAT_CHARS_MIN_MIN = 4;
const REWRITE_BEAT_CHARS_MIN_MAX = 50;
const REWRITE_BEAT_CHARS_MAX_MIN = 4;
const REWRITE_BEAT_CHARS_MAX_MAX = 80;

// While typing, parse the raw value WITHOUT clamping to the min — clamping a
// half-typed value (e.g. "1" → min 4) would snap it back and make the next
// digit append instead of replace (typing 18 yielded 48). Min is enforced on
// blur via `clampRewriteNumber`.
function parseRewriteNumber(value: string, fallback: number) {
  if (value.trim() === "") return fallback;
  const next = Number(value);
  return Number.isFinite(next) ? Math.round(next) : fallback;
}

function clampRewriteNumber(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, Math.round(value)));
}

function ScriptTabContent() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [planningErrors, setPlanningErrors] = useState<Partial<Record<PlanningAssetKind, string>>>({});
  const reportPlanningError = (kind: PlanningAssetKind, cause: unknown) => {
    const error = backendErrorToastMessage(cause, t);
    setPlanningErrors((previous) => ({ ...previous, [kind]: error }));
    toast.error(error);
  };
  const { project, episode } = Route.useParams();
  const epNum = parseInt(episode, 10);
  const scriptHash = useRouterState({ select: (state) => state.location.hash });
  const [workspace, setWorkspace] = useState<"source" | "semantics" | "review" | "assets">(() => scriptHash.replace(/^#/, "") === "script-assets" ? "assets" : scriptHash.replace(/^#/, "") === "script-source" ? "source" : "semantics");
  useEffect(() => {
    const target = scriptHash.replace(/^#/, "");
    if (target === "script-assets") setWorkspace("assets");
    else if (target === "script-source") setWorkspace("source");
  }, [scriptHash, project, epNum]);
  const creationDocument = new URLSearchParams(window.location.search).get("creationDocument");
  const queryClient = useQueryClient();
  const { data: episodeRes } = useEpisodeDetail(project, epNum);
  const { data: projectRes } = useProject(project);
  const { data: beatsRes, isLoading: beatsLoading } = useEpisodeBeats(
    project,
    epNum,
  );
  const directorPlans = useDirectorPlans(project, epNum);
  const planningBlocked = assetPlanningBlockReason(directorPlans);
  const { data: charactersRes } = useCharacters(project);
  const updateEpisode = useUpdateEpisode(project);
  const planIdentities = usePlanIdentities(project);
  const planIdentitiesCost = useGenerationCreditCost("feature", "identity_planner");
  const planIdentitiesCostDisplay =
    planIdentitiesCost.data?.data.display ??
    (planIdentitiesCost.error instanceof BillingRuleNotConfiguredError
      ? t("common.billingRuleNotConfiguredShort")
      : null);
  const planScenes = usePlanEpisodeScenes(project);
  const planScenesCost = useGenerationCreditCost("feature", "episode_scene_planner");
  const planScenesCostDisplay =
    planScenesCost.data?.data.display ??
    (planScenesCost.error instanceof BillingRuleNotConfiguredError
      ? t("common.billingRuleNotConfiguredShort")
      : null);
  const planProps = usePlanEpisodeProps(project);
  const planPropsCost = useGenerationCreditCost("feature", "episode_prop_planner");
  const planPropsCostDisplay =
    planPropsCost.data?.data.display ??
    (planPropsCost.error instanceof BillingRuleNotConfiguredError
      ? t("common.billingRuleNotConfiguredShort")
      : null);
  const generateRewrite = useGenerateRewrite(project, epNum);

  const episodeData = episodeRes?.data;
  const characters: Character[] = charactersRes?.data ?? [];
  const identityIds = episodeData?.identity_ids ?? [];
  const identityDefaultMap = episodeData?.identity_default_map ?? {};
  const rawContent = episodeData?.raw_content ?? "";
  const sourceText = episodeData?.beat_source_text ?? "";
  const sourceTextForEditor = sourceText || rawContent;
  const sceneMenu = episodeData?.scene_menu ?? [];
  const propMenu = episodeData?.prop_menu ?? [];
  const beats = beatsRes?.data ?? [];
  const planRevisions = directorPlans.data?.ok ? directorPlans.data.data : [];
  const directorPlan = planRevisions.find((item) => item.status === "review_required")
    ?? planRevisions.find((item) => item.status === "active")
    ?? planRevisions[0];
  const directorShotCount = directorPlan?.groups.reduce(
    (count, group) => count + group.shots.length,
    0,
  ) ?? 0;
  const isNarratedProject = projectRes?.data?.spine_template === "narrated";

  const [pickerOpen, setPickerOpen] = useState(false);
  const [assetCategory, setAssetCategory] =
    useState<AssetPlanningCategory>("identities");
  const [rewriteTargetBeats, setRewriteTargetBeats] = useState(18);
  const [rewriteBeatCharsMin, setRewriteBeatCharsMin] = useState(14);
  const [rewriteBeatCharsMax, setRewriteBeatCharsMax] = useState(20);
  const initializedSourceRef = useRef("");

  const sourceScope = saveScopes.episodeSource(project, epNum);
  const identitiesScope = saveScopes.episodeIdentities(project, epNum);

  useEffect(() => {
    const initKey = `${project}:${epNum}`;
    if (!episodeData || initializedSourceRef.current === initKey) return;
    if (sourceText.trim() || !rawContent.trim()) return;

    initializedSourceRef.current = initKey;
    void trackSave(sourceScope, () =>
      updateEpisode.mutateAsync({
        episodeNum: epNum,
        data: { beat_source_text: rawContent },
      }),
    )
      .then(() =>
        queryClient.invalidateQueries({
          queryKey: queryKeys.episodeDetail(project, epNum),
        }),
      )
      .catch(() => {
        initializedSourceRef.current = "";
        toast.error(t("common.error"));
      });
  }, [
    episodeData,
    epNum,
    project,
    queryClient,
    rawContent,
    sourceScope,
    sourceText,
    t,
    updateEpisode,
  ]);

  const invalidateIdentityData = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.episodes(project) });
    queryClient.invalidateQueries({
      queryKey: queryKeys.episodeDetail(project, epNum),
    });
    queryClient.invalidateQueries({ queryKey: queryKeys.characters(project) });
    queryClient.invalidateQueries({ queryKey: queryKeys.pipelineStatus(project) });
    for (const character of characters) {
      queryClient.invalidateQueries({
        queryKey: queryKeys.identities(project, character.name),
      });
    }
  };

  const notifyIdentityPlanResult = (result: unknown) => {
    const data = (result ?? {}) as {
      new_count?: number;
      resolved_count?: number;
    };
    if ((data.new_count ?? 0) > 0) {
      toast.success(
        t("episode.script.planIdentitiesNew", { count: data.new_count }),
      );
    } else if ((data.resolved_count ?? 0) > 0) {
      toast.success(
        t("episode.script.planIdentitiesResolved", {
          count: data.resolved_count,
        }),
      );
    } else {
      toast.warning(t("episode.script.planIdentitiesNone"));
    }
  };

  const identityTask = useTaskController({
    key: { taskType: TASK_TYPES.IDENTITY_PLANNER, project, episode: epNum },
    invalidateKeys: [
      queryKeys.episodes(project),
      queryKeys.episodeDetail(project, epNum),
      queryKeys.characters(project),
      queryKeys.pipelineStatus(project),
    ],
    showCompleteToast: false,
    onComplete: (result) => {
      invalidateIdentityData();
      notifyIdentityPlanResult(result);
    },
  });

  // 场景/道具规划在 EE 下是异步任务：POST 只负责入队，请求返回时任务才刚开始。
  // 按钮的转圈必须跟着任务流走（含刷新页面后的重连），否则会在入队瞬间就停下，
  // 和任务中心的「生成中」对不上。
  const sceneTask = useTaskController({
    key: { taskType: TASK_TYPES.EPISODE_SCENE_PLANNER, project, episode: epNum },
    invalidateKeys: [
      queryKeys.episodes(project),
      queryKeys.episodeDetail(project, epNum),
      queryKeys.scenes(project),
      queryKeys.pipelineStatus(project),
    ],
    showCompleteToast: false,
    onComplete: (result) => {
      const data = (result ?? {}) as { total_count?: number };
      toast.success(
        t("episode.script.scenePlanComplete", { count: data.total_count ?? 0 }),
      );
    },
  });

  const propTask = useTaskController({
    key: { taskType: TASK_TYPES.EPISODE_PROP_PLANNER, project, episode: epNum },
    invalidateKeys: [
      queryKeys.episodes(project),
      queryKeys.episodeDetail(project, epNum),
      queryKeys.props(project),
      queryKeys.pipelineStatus(project),
    ],
    showCompleteToast: false,
    onComplete: (result) => {
      const data = (result ?? {}) as { total_count?: number };
      toast.success(
        t("episode.script.propPlanComplete", { count: data.total_count ?? 0 }),
      );
    },
  });

  const saveField = async (
    data: Parameters<typeof updateEpisode.mutateAsync>[0]["data"],
  ) => {
    try {
      await trackSave(identitiesScope, () =>
        updateEpisode.mutateAsync({ episodeNum: epNum, data }),
      );
    } catch {
      toast.error(t("common.error"));
    }
  };

  const handleIdentityChange = (
    next: string[],
    nextDefaultMap: Record<string, string>,
  ) => {
    void saveField({
      identity_ids: next,
      identity_default_map: nextDefaultMap,
    });
  };

  const handleSourceSave = async (next: string) => {
    const savePromise = trackSave(sourceScope, () =>
      updateEpisode.mutateAsync({
        episodeNum: epNum,
        data: { beat_source_text: next },
      }),
    );
    try {
      await toast
        .promise(savePromise, {
          loading: t("common.saveStatus.saving"),
          success: t("common.saveStatus.saved"),
          error: t("common.saveStatus.error"),
        })
        .unwrap();
    } catch {
      // toast.promise already renders the failure state.
    }
  };

  const handleGenerateRewrite = async () => {
    if (rewriteBeatCharsMin > rewriteBeatCharsMax) {
      toast.error(t("episode.script.minGtMax"));
      return;
    }

    try {
      const rewriteRes = await generateRewrite.mutateAsync({
        target_beats: rewriteTargetBeats,
        beat_chars_min: rewriteBeatCharsMin,
        beat_chars_max: rewriteBeatCharsMax,
      });
      if (rewriteRes.ok === false) {
        toast.error(rewriteRes.error || t("common.error"));
        return;
      }
      await queryClient.invalidateQueries({
        queryKey: queryKeys.episodeDetail(project, epNum),
      });
      toast.success(t("episode.script.rewriteComplete"));
    } catch {
      toast.error(t("common.error"));
    }
  };

  const handlePlanIdentities = async () => {
    if (planningBlocked) { toast.warning(planningBlocked); return; }
    setPlanningErrors((previous) => ({ ...previous, character: "" }));
    try {
      const res = await planIdentities.mutateAsync(epNum);
      if (res.ok === false) {
        reportPlanningError("character", res.error);
        return;
      }
      identityTask.start({ scope: res.scope });
    } catch (err) {
      reportPlanningError("character", err);
    }
  };

  const handlePlanScenes = async () => {
    if (planningBlocked) { toast.warning(planningBlocked); return; }
    setPlanningErrors((previous) => ({ ...previous, scene: "" }));
    try {
      const res = await planScenes.mutateAsync(epNum);
      if (res.ok === false) {
        reportPlanningError("scene", res.error);
        return;
      }
      // CE 走同步规划，直接返回结果；EE 只是入队，交给任务控制器跟进行中状态。
      if (isPlanEpisodeAssetsResult(res)) {
        toast.success(
          t("episode.script.scenePlanComplete", { count: res.data.total_count }),
        );
        return;
      }
      sceneTask.start({ scope: res.scope });
    } catch (err) {
      reportPlanningError("scene", err);
    }
  };

  const handlePlanProps = async () => {
    if (planningBlocked) { toast.warning(planningBlocked); return; }
    setPlanningErrors((previous) => ({ ...previous, prop: "" }));
    try {
      const res = await planProps.mutateAsync(epNum);
      if (res.ok === false) {
        reportPlanningError("prop", res.error);
        return;
      }
      if (isPlanEpisodeAssetsResult(res)) {
        toast.success(
          t("episode.script.propPlanComplete", { count: res.data.total_count }),
        );
        return;
      }
      propTask.start({ scope: res.scope });
    } catch (err) {
      reportPlanningError("prop", err);
    }
  };

  const identityPlanning = planIdentities.isPending || identityTask.started;

  return (
    <div className="flex h-full flex-col overflow-hidden bg-background">
      {(creationDocument || isNarratedProject) && <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-border/30 px-5 py-2 text-xs">
        {creationDocument && <a className="text-primary underline" href={`/projects/${encodeURIComponent(project)}/creation?document=${encodeURIComponent(creationDocument)}`}>返回创作文档</a>}
        <span className="text-muted-foreground">叙事组脚本 · 读取原文 → 校对场次 → 审核与激活镜头方案</span>

        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          {isNarratedProject && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex h-7 items-center gap-1.5 text-[11px] text-muted-foreground">
                  <span className="shrink-0 whitespace-nowrap">
                    {t("episode.script.rewriteTargetBeats")}
                  </span>
                  <Input
                    type="number"
                    min={REWRITE_TARGET_BEATS_MIN}
                    max={REWRITE_TARGET_BEATS_MAX}
                    step={1}
                    value={rewriteTargetBeats}
                    disabled={generateRewrite.isPending}
                    onChange={(event) =>
                      setRewriteTargetBeats(
                        parseRewriteNumber(event.target.value, rewriteTargetBeats),
                      )
                    }
                    onBlur={() =>
                      setRewriteTargetBeats((value) =>
                        clampRewriteNumber(
                          value,
                          REWRITE_TARGET_BEATS_MIN,
                          REWRITE_TARGET_BEATS_MAX,
                        ),
                      )
                    }
                    className="h-7 w-14 rounded-[7px] px-2 text-xs tabular-nums"
                  />
                </label>
                <label className="flex h-7 items-center gap-1.5 text-[11px] text-muted-foreground">
                  <span className="shrink-0 whitespace-nowrap">
                    {t("episode.script.rewriteBeatCharsMin")}
                  </span>
                  <Input
                    type="number"
                    min={REWRITE_BEAT_CHARS_MIN_MIN}
                    max={REWRITE_BEAT_CHARS_MIN_MAX}
                    step={1}
                    value={rewriteBeatCharsMin}
                    disabled={generateRewrite.isPending}
                    onChange={(event) =>
                      setRewriteBeatCharsMin(
                        parseRewriteNumber(event.target.value, rewriteBeatCharsMin),
                      )
                    }
                    onBlur={() =>
                      setRewriteBeatCharsMin((value) =>
                        clampRewriteNumber(
                          value,
                          REWRITE_BEAT_CHARS_MIN_MIN,
                          REWRITE_BEAT_CHARS_MIN_MAX,
                        ),
                      )
                    }
                    className="h-7 w-14 rounded-[7px] px-2 text-xs tabular-nums"
                  />
                </label>
                <label className="flex h-7 items-center gap-1.5 text-[11px] text-muted-foreground">
                  <span className="shrink-0 whitespace-nowrap">
                    {t("episode.script.rewriteBeatCharsMax")}
                  </span>
                  <Input
                    type="number"
                    min={REWRITE_BEAT_CHARS_MAX_MIN}
                    max={REWRITE_BEAT_CHARS_MAX_MAX}
                    step={1}
                    value={rewriteBeatCharsMax}
                    disabled={generateRewrite.isPending}
                    onChange={(event) =>
                      setRewriteBeatCharsMax(
                        parseRewriteNumber(event.target.value, rewriteBeatCharsMax),
                      )
                    }
                    onBlur={() =>
                      setRewriteBeatCharsMax((value) =>
                        clampRewriteNumber(
                          value,
                          REWRITE_BEAT_CHARS_MAX_MIN,
                          REWRITE_BEAT_CHARS_MAX_MAX,
                        ),
                      )
                    }
                    className="h-7 w-14 rounded-[7px] px-2 text-xs tabular-nums"
                  />
                </label>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={handleGenerateRewrite}
                disabled={generateRewrite.isPending}
                className="h-7 gap-1.5 rounded-[7px] border-primary/35 bg-primary/[0.08] px-2.5 text-xs font-normal text-primary shadow-none hover:border-primary/55 hover:bg-primary/[0.14] hover:text-primary [&_svg]:size-3.5"
              >
                {generateRewrite.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Sparkles className="size-3.5" />
                )}
                {t("episode.script.generateRewrite")}
              </Button>
            </>
          )}
        </div>
      </div>}

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[188px_minmax(0,1fr)]">
        <aside aria-label="脚本制作步骤" className="flex gap-2 overflow-x-auto border-b border-white/10 bg-white/[0.015] p-3 lg:block lg:overflow-y-auto lg:border-r lg:border-b-0">
          <h2 className="mb-3 hidden text-sm font-semibold lg:block">场次校对与镜头规划</h2>
          {([["source","读取本集原文"],["semantics","解析与校对"],["review","审核镜头方案"],["assets","核对本集资产"]] as const).map(([key,label],index) => <button key={key} type="button" aria-current={workspace === key ? "step" : undefined} className={`mb-2 flex w-full shrink-0 items-center gap-2 rounded-md border px-3 py-2.5 text-left text-xs lg:shrink ${workspace === key ? "border-primary/40 bg-primary/10 text-primary" : "border-transparent text-muted-foreground hover:bg-white/5"}`} onClick={() => setWorkspace(key)}><span className="opacity-60">{index + 1}</span>{label}</button>)}
          <div className="mt-5 hidden space-y-3 border-t border-white/10 pt-4 text-xs lg:block">
            <p className="text-muted-foreground">本集来源</p><p>{sourceTextForEditor?.trim().split(/\r?\n/).filter(Boolean).length ?? 0} 行原文 · {identityIds.length} 个人物身份</p>
            <p className="text-muted-foreground">镜头方案</p><p>{directorPlan ? `${directorPlan.groups.length} 个叙事组 · ${directorShotCount} 个镜头` : "尚未生成"}</p>
            <p className="text-muted-foreground leading-6">候选方案需审核并激活后才会用于分镜生图。旧媒体和历史版本保留。</p>
            <Button size="sm" variant="outline" className="w-full" nativeButton={false} render={<Link to="/projects/$project/episodes/$episode/beats" params={{project,episode}} search={{sub:"render"} as never} />}>进入分镜生图 →</Button>
          </div>
        </aside>
        <div className="min-h-0 min-w-0 overflow-hidden">
          <section hidden={workspace !== "source"} aria-label="本集原文工作区" className="h-full overflow-y-auto p-4"><div id="script-source" className="order-1 scroll-mt-4">
            <p className="mb-3 text-xs text-muted-foreground">先保存剧本修改，再重新解析场次并校对。镜头方案以已确认的校对版本为准。</p>
            <EpisodeSourceEditor
              rawContent={rawContent}
              sourceText={sourceTextForEditor}
              saving={updateEpisode.isPending}
              onSave={handleSourceSave}
              labels={{
                rawLabel: t("episode.script.rawLabel"),
                rawActionLabel: t("episode.script.rawActionLabel"),
                noRawText: t("episode.script.noRawText"),
                sourceLabel: t(
                  isNarratedProject
                    ? "episode.script.sourceTextLabelNarrated"
                    : "episode.script.sourceTextLabelDrama",
                ),
                sourceMeta: (count) =>
                  t("episode.script.sourceTextMeta", { count }),
                sourcePlaceholder: t(
                  isNarratedProject
                    ? "episode.script.sourceTextPlaceholderNarrated"
                    : "episode.script.sourceTextPlaceholderDrama",
                ),
                linePreviewLabel: t("episode.script.linePreviewLabel"),
                lineCount: (count) => t("episode.script.lineCount", { count }),
                noLines: t("episode.script.noSourceLines"),
              }}
              className="min-w-0"
            />
            </div><details className="mt-4 rounded-lg border border-white/10 p-3"><summary className="cursor-pointer text-sm text-muted-foreground">逐镜头文案与对白预览</summary><ScriptBeatPreview beats={beats} loading={beatsLoading} labels={{
                title: t("episode.script.previewTitle"),
                count: count => t("episode.script.previewCount", {count}),
                loading: t("episode.script.previewLoading"),
                emptyTitle: t("episode.script.previewEmptyTitle"),
                empty: t("episode.script.previewEmpty"),
                audioType: type => t(`audioType.${type}`, {defaultValue:type}),
                speaker: t("episode.script.previewSpeaker"), noSpeaker:t("episode.script.previewNoSpeaker"),
                dialogueLine:t("episode.script.previewDialogueLine"), narrationLine:t("episode.script.previewNarrationLine"),
                noNarration:t("episode.script.previewNoNarration"), visualDescription:t("episode.script.previewVisualDescription"),
                noVisualDescription:t("episode.script.previewNoVisualDescription")
              }} /></details></section>
          <section hidden={workspace !== "semantics"} aria-label="场次校对工作区" className="h-full overflow-y-auto p-3"><ScreenplayWorkbench compact project={project} episode={epNum} onReviewRequest={() => setWorkspace("review")} /></section>
          <section hidden={workspace !== "assets"} aria-label="本集资产核对" className="h-full overflow-y-auto p-4"><section id="script-assets" className="rounded-xl border border-white/10 p-4">
              <div className="mb-2 flex h-7 items-center justify-between gap-3">
                <h2 className="text-sm font-semibold tracking-tight text-foreground">
                  {t("episode.script.assetPlanningTitle")}
                </h2>
                <Select
                  value={assetCategory}
                  onValueChange={(value) =>
                    setAssetCategory(value as AssetPlanningCategory)
                  }
                >
                  <SelectTrigger
                    size="sm"
                    className="inline-flex !h-6 w-[112px] shrink-0 items-center gap-1 !rounded-[6px] !border !border-white/[0.12] !bg-white/[0.04] px-2 text-[11px] font-normal text-foreground/78 shadow-none hover:!border-white/[0.2] hover:!bg-white/[0.05] hover:text-foreground focus-visible:!border-white/24 focus-visible:!ring-0 [&_svg]:!size-3"
                  >
                    {/* base-ui Select.Value renders the raw value by default — map
                        it to the localized label so the trigger shows 中文. */}
                    <SelectValue>
                      {(value) =>
                        value === "scenes"
                          ? t("episode.script.scenes")
                          : value === "props"
                            ? t("episode.script.props")
                            : t("episode.script.identities")
                      }
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent alignItemWithTrigger={false}>
                    <SelectItem value="identities">
                      {t("episode.script.identities")}
                    </SelectItem>
                    <SelectItem value="scenes">
                      {t("episode.script.scenes")}
                    </SelectItem>
                    <SelectItem value="props">
                      {t("episode.script.props")}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <AssetPlanningPrerequisite reason={planningBlocked} onRetry={directorPlans.isError ? () => { void directorPlans.refetch(); } : undefined} onOpenPlan={() => setWorkspace("semantics")} />
              {([["character", planningErrors.character || identityTask.stream.error], ["scene", planningErrors.scene || sceneTask.stream.error], ["prop", planningErrors.prop || propTask.stream.error]] as const).map(([kind, error]) => <AssetPlanningFailure key={kind} kind={kind} error={error} onOpenAssets={(type) => { void navigate({ to: "/projects/$project/characters", params: { project }, search: { type } as never }); }} />)}
              <EpisodeAssetPlanning
                planningBlocked={!!planningBlocked}
                project={project}
                selectedCategory={assetCategory}
                characters={characters}
                selectedIdentityIds={identityIds}
                identityDefaultMap={identityDefaultMap}
                sceneMenu={sceneMenu}
                propMenu={propMenu}
                identityPending={identityPlanning}
                scenePending={planScenes.isPending || sceneTask.started}
                propPending={planProps.isPending || propTask.started}
                sceneCostDisplay={planScenesCostDisplay}
                propCostDisplay={planPropsCostDisplay}
                onPlanIdentities={() => setPickerOpen(true)}
                onIdentityChange={handleIdentityChange}
                onPlanScenes={handlePlanScenes}
                onPlanProps={handlePlanProps}
                labels={{
                  identities: t("episode.script.identities"),
                  scenes: t("episode.script.scenes"),
                  props: t("episode.script.props"),
                  noIdentities: t("episode.script.noIdentities"),
                  noScenes: t("episode.script.noScenes"),
                  noProps: t("episode.script.noProps"),
                  planIdentities: t("episode.script.planIdentities"),
                  replanIdentities: t("episode.script.replanIdentities"),
                  defaultIdentity: t("identityPicker.defaultIdentity"),
                  planScenes: t("episode.script.planScenes"),
                  replanScenes: t("episode.script.replanScenes"),
                  planProps: t("episode.script.planProps"),
                  replanProps: t("episode.script.replanProps"),
                  propInGlobal: t("episode.script.propInGlobal"),
                  propCheckingGlobal: t("episode.script.propCheckingGlobal"),
                  promoteProp: t("episode.script.promoteProp"),
                  promotePropTitle: (name) =>
                    t("episode.script.promotePropTitle", { name }),
                  promotePropName: t("episode.script.promotePropName"),
                  promotePropType: t("episode.script.promotePropType"),
                  promoteVisualPrompt: t("episode.script.promoteVisualPrompt"),
                  promoteOwner: t("episode.script.promoteOwner"),
                  promoteSubmit: t("episode.script.promoteSubmit"),
                  promoteCancel: t("common.cancel"),
                  propTypeLabel: (value) =>
                    t(`assets.props.types.${value}`, { defaultValue: value }),
                  promoteSuccess: t("episode.script.promoteSuccess"),
                }}
              />
            </section></section>
          {workspace === "review" && <DirectorReviewWorkbench project={project} episode={epNum} onClose={() => setWorkspace("semantics")} />}
        </div>
      </div>

      <IdentityPickerDialog
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        project={project}
        characters={characters}
        selected={identityIds}
        defaultMap={identityDefaultMap}
        onChange={handleIdentityChange}
        onPlan={handlePlanIdentities}
        planPending={identityPlanning}
        planDisabled={!!planningBlocked}
        planCostDisplay={planIdentitiesCostDisplay}
      />
    </div>
  );
}

export const Route = createLazyFileRoute(
  "/_app/projects/$project/episodes/$episode/script",
)({
  component: ScriptTabContent,
});
