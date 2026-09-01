import { Loader2, Play, ShieldCheck, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { useTaskController } from "@/hooks/use-task-controller";
import { useCreateDirectorPlan } from "@/lib/queries/director-plans";
import {
  useActivateScreenplaySemantics,
  useCreateScreenplaySemantics,
  useEditScreenplaySemantics,
  useRepairScreenplaySemantics,
  useRetrySemanticScene,
  useScreenplaySemantics,
  screenplaySemanticKeys,
} from "@/lib/queries/screenplay-semantics";
import { TASK_TYPES } from "@/lib/task-types";
import { BeatEditor } from "./beat-editor";
import { EvidenceInspector } from "./evidence-inspector";
import { SceneTree } from "./scene-tree";

export function ScreenplayWorkbench({ project, episode }: { project: string; episode: number }) {
  const semantics = useScreenplaySemantics(project, episode);
  const create = useCreateScreenplaySemantics(project, episode);
  const retry = useRetrySemanticScene(project, episode);
  const edit = useEditScreenplaySemantics(project, episode);
  const repair = useRepairScreenplaySemantics(project, episode);
  const activate = useActivateScreenplaySemantics(project, episode);
  const director = useCreateDirectorPlan(project, episode);
  const repairTask = useTaskController({
    key: { project, episode, taskType: TASK_TYPES.SCREENPLAY_SEMANTIC_REPAIR },
    invalidateKeys: [screenplaySemanticKeys.all(project, episode)],
    showCompleteToast: false,
  });
  const data = semantics.data?.ok ? semantics.data.data : undefined;
  const revision = data?.revisions[0]
    ?? data?.revisions.find((item) => item.revision_id === data.active_revision_id);
  const [sceneId, setSceneId] = useState("");
  const [beatId, setBeatId] = useState("");
  const selectedScene = revision?.scenes.find((item) => item.id === sceneId) ?? revision?.scenes[0];
  const sceneBeats = useMemo(() => revision?.beats.filter((item) => item.scene_id === selectedScene?.id).sort((a, b) => a.ordinal - b.ordinal) ?? [], [revision, selectedScene]);
  const selectedBeat = sceneBeats.find((item) => item.id === beatId) ?? sceneBeats[0];
  const issues = revision?.validation_report.issues.filter((item) => !item.scene_id || item.scene_id === selectedScene?.id) ?? [];
  const pending = create.isPending || retry.isPending || edit.isPending || repair.isPending || repairTask.started || activate.isPending || director.isPending;
  const errorCount = revision?.validation_report.issues.filter((issue) => issue.severity === "error").length ?? 0;
  const repairDisabledReason = pending
    ? "已有导演拆解任务正在运行"
    : revision?.validation_report.passed
      ? "当前拆解已通过校验，无需修复"
      : undefined;
  const activateDisabledReason = pending
    ? "已有导演拆解任务正在运行"
    : !revision?.validation_report.passed
      ? `当前拆解仍有 ${errorCount} 个校验问题，请先修复并重新校验`
      : data?.active_revision_id === revision?.revision_id
        ? "当前拆解已经激活"
        : undefined;
  const directorDisabledReason = pending
    ? "已有导演拆解任务正在运行"
    : !data?.active_revision_id
      ? "请先人工激活一个拆解版本"
      : undefined;

  useEffect(() => {
    if (selectedScene && selectedScene.id !== sceneId) setSceneId(selectedScene.id);
  }, [sceneId, selectedScene]);
  useEffect(() => {
    if (selectedBeat && selectedBeat.id !== beatId) setBeatId(selectedBeat.id);
  }, [beatId, selectedBeat]);

  if (semantics.isLoading) return <div className="flex h-64 items-center justify-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />加载剧本语义…</div>;

  return <section className="overflow-hidden rounded-xl border border-white/10 bg-black/10" aria-label="剧本导演拆解工作台">
    <header className="flex flex-wrap items-center gap-2 border-b border-white/10 p-3">
      <div className="mr-auto"><h2 className="text-sm font-semibold">剧本校对与导演拆解</h2><p className="text-xs text-muted-foreground">Scene → Dramatic Beat → 原文证据；编辑只派生版本，不触发付费媒体。</p></div>
      <div className="flex flex-wrap items-center gap-2" aria-label="导演拆解操作">
        <Button size="sm" variant="outline" disabled={pending} title={pending ? "已有导演拆解任务正在运行" : undefined} onClick={() => create.mutate([])}><Play />解析场次</Button>
        {revision && <Button size="sm" variant="outline" disabled={Boolean(repairDisabledReason)} title={repairDisabledReason} onClick={() => void repair.mutateAsync({ revisionId: revision.revision_id }).then((started) => repairTask.start({ scope: started.scope })).catch(() => undefined)}><Sparkles />委托 Runtime 修复</Button>}
        {revision && <Button size="sm" variant="outline" disabled={Boolean(activateDisabledReason)} title={activateDisabledReason} onClick={() => activate.mutate({ revisionId: revision.revision_id })}><ShieldCheck />激活拆解</Button>}
        <Button size="sm" disabled={Boolean(directorDisabledReason)} title={directorDisabledReason} onClick={() => director.mutate()}>生成镜头方案</Button>
      </div>
    </header>
    {(repair.isPending || repairTask.started) && <div role="status" className="border-b border-cyan-400/20 bg-cyan-400/[0.06] px-3 py-2 text-xs text-cyan-100">{repairTask.stream.currentTask || "正在提交导演拆解 Runtime 修复"}（默认并发 3，最多 2 轮）。不会修改剧本原文，完成后仍需人工激活。</div>}
    {repair.isError && <div role="alert" className="border-b border-red-400/20 bg-red-400/[0.06] px-3 py-2 text-xs text-red-200">Runtime 修复提交失败：{repair.error instanceof Error ? repair.error.message : "未知错误"}</div>}
    {repairTask.stream.status === "failed" && <div role="alert" className="border-b border-red-400/20 bg-red-400/[0.06] px-3 py-2 text-xs text-red-200">Runtime 修复失败：{repairTask.stream.error || "未知错误"}</div>}
    {!revision ? <div className="flex h-72 flex-col items-center justify-center gap-3 text-center"><p className="text-sm text-muted-foreground">尚未解析。本入口只拆解已有剧本，不改写剧情。</p><Button disabled={pending} onClick={() => create.mutate([])}>解析场次</Button></div> : <>
      <div className="flex items-center gap-3 border-b border-white/10 px-3 py-2 text-xs text-muted-foreground"><span>{revision.revision_id}</span><span>{revision.status}</span><span>{revision.scenes.length} 场 · {revision.beats.length} 个戏剧节拍</span><span className={revision.validation_report.passed ? "text-emerald-400" : "text-amber-400"}>{revision.validation_report.passed ? "校验通过" : `${revision.validation_report.issues.length} 个问题`}</span></div>
      <div className="grid h-[560px] min-h-0 grid-cols-[220px_minmax(360px,1fr)_minmax(280px,0.8fr)]">
        <SceneTree scenes={revision.scenes} selectedId={selectedScene?.id ?? ""} disabled={pending} onSelect={(id) => { setSceneId(id); setBeatId(""); }} onRetry={(id) => retry.mutate({ revisionId: revision.revision_id, sceneId: id })} />
        <BeatEditor beats={sceneBeats} selectedId={selectedBeat?.id ?? ""} disabled={pending || !["draft", "review_required", "active"].includes(revision.status)} onSelect={setBeatId} onSplit={(beat) => {
          const range = beat.source_ranges[0];
          if (!range || range.start_line >= range.end_line) return;
          edit.mutate({ revisionId: revision.revision_id, command: { type: "split", beat_id: beat.id, before_line: Math.floor((range.start_line + range.end_line) / 2) + 1 } });
        }} onMerge={(first, second) => edit.mutate({ revisionId: revision.revision_id, command: { type: "merge", first_beat_id: first.id, second_beat_id: second.id } })}
        onReorder={(beatIds) => selectedScene && edit.mutate({ revisionId: revision.revision_id, command: { type: "reorder", scene_id: selectedScene.id, beat_ids: beatIds } })}
        onUpdate={(beat, updates) => edit.mutate({ revisionId: revision.revision_id, command: { type: "update", beat_id: beat.id, goal: updates.goal, obstacle: updates.obstacle, action: updates.action, reaction: updates.reaction, turn: updates.turn, result: updates.result, emotional_shift: updates.emotional_shift, estimated_duration_seconds: updates.estimated_duration_seconds } })} />
        <EvidenceInspector scene={selectedScene} beat={selectedBeat} issues={issues} />
      </div>
    </>}
  </section>;
}
