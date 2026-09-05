// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createLazyFileRoute, Link } from "@tanstack/react-router";
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
import { EpisodeHealthSummary } from "@/components/episode/health-bar";
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
  const { project, episode } = Route.useParams();
  const epNum = parseInt(episode, 10);
  const queryClient = useQueryClient();
  const { data: episodeRes } = useEpisodeDetail(project, epNum);
  const { data: projectRes } = useProject(project);
  const { data: beatsRes, isLoading: beatsLoading } = useEpisodeBeats(
    project,
    epNum,
  );
  const directorPlans = useDirectorPlans(project, epNum);
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
    try {
      const res = await planIdentities.mutateAsync(epNum);
      if (res.ok === false) {
        toast.error(backendErrorToastMessage(res.error, t));
        return;
      }
      identityTask.start({ scope: res.scope });
    } catch (err) {
      toast.error(backendErrorToastMessage(err, t));
    }
  };

  const handlePlanScenes = async () => {
    try {
      const res = await planScenes.mutateAsync(epNum);
      if (res.ok === false) {
        toast.error(backendErrorToastMessage(res.error, t));
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
      toast.error(backendErrorToastMessage(err, t));
    }
  };

  const handlePlanProps = async () => {
    try {
      const res = await planProps.mutateAsync(epNum);
      if (res.ok === false) {
        toast.error(backendErrorToastMessage(res.error, t));
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
      toast.error(backendErrorToastMessage(err, t));
    }
  };

  const identityPlanning = planIdentities.isPending || identityTask.started;

  return (
    <div className="flex h-full flex-col overflow-hidden bg-background">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-border/30 px-5 py-3 text-xs">
        <EpisodeHealthSummary
          project={project}
          episode={epNum}
          className="pr-1"
        />
        <div className="inline-flex h-7 items-center gap-2 text-muted-foreground">
          <span className="text-[11px]">{t("episode.script.modeLabel")}</span>
          <span className="text-[11px] text-foreground/68">
            专业剧本语义模式
          </span>
        </div>

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
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="px-5 pt-5">
          <ScreenplayWorkbench project={project} episode={epNum} />
        </div>
        <div className="grid min-h-0 gap-5 px-5 pb-5 pt-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div className="min-w-0 space-y-5">
            <section>
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
              <EpisodeAssetPlanning
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
            </section>

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
          </div>

          <div className="min-w-0">
            {directorPlan ? (
              <section className="rounded-xl border border-emerald-400/20 bg-emerald-400/[0.04] p-5" aria-label="导演镜头方案摘要">
                <div className="flex flex-wrap items-start gap-3">
                  <div className="mr-auto">
                    <h2 className="text-sm font-semibold text-foreground">镜头方案已生成</h2>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {directorPlan.status === "review_required" ? "待人工审核" : directorPlan.status === "active" ? "已激活" : directorPlan.status}
                    </p>
                  </div>
                  <Button size="sm" variant="outline" nativeButton={false} render={<Link to="/projects/$project/episodes/$episode/beats" params={{ project, episode }} />}>审核镜头方案</Button>
                </div>
                <div className="mt-5 grid grid-cols-2 gap-3">
                  <div className="rounded-lg border border-white/10 bg-black/10 p-3">
                    <div className="text-[11px] text-muted-foreground">叙事组</div>
                    <div className="mt-1 text-xl font-semibold tabular-nums">{directorPlan.groups.length}</div>
                  </div>
                  <div className="rounded-lg border border-white/10 bg-black/10 p-3">
                    <div className="text-[11px] text-muted-foreground">镜头</div>
                    <div className="mt-1 text-xl font-semibold tabular-nums">{directorShotCount}</div>
                  </div>
                </div>
              </section>
            ) : (
              <ScriptBeatPreview
                beats={beats}
                loading={beatsLoading}
                className="px-0 pb-0"
                labels={{
                  title: t("episode.script.previewTitle"),
                  count: (count) => t("episode.script.previewCount", { count }),
                  loading: t("episode.script.previewLoading"),
                  emptyTitle: t("episode.script.previewEmptyTitle"),
                  empty: t("episode.script.previewEmpty"),
                  audioType: (type) =>
                    t(`audioType.${type}`, { defaultValue: type }),
                  speaker: t("episode.script.previewSpeaker"),
                  noSpeaker: t("episode.script.previewNoSpeaker"),
                  dialogueLine: t("episode.script.previewDialogueLine"),
                  narrationLine: t("episode.script.previewNarrationLine"),
                  noNarration: t("episode.script.previewNoNarration"),
                  visualDescription: t("episode.script.previewVisualDescription"),
                  noVisualDescription: t("episode.script.previewNoVisualDescription"),
                }}
              />
            )}
          </div>
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
