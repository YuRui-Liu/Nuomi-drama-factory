import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import { jsonWithBackendError } from "@/lib/api-errors";
import { queryKeys } from "@/lib/query-keys";
import { useTasks } from "@/lib/queries/tasks";
import type { NarrativeStageState } from "@/lib/queries/narrative-groups";

const activeStatuses = new Set(["submitting", "pending", "queued", "starting", "running"]);
const decisionLabels = { acknowledged: "已查看，保留问题", needs_repair: "需要修复", dismissed: "人工认为误报" };

export function GroupVideoQualityControls({ project, episode, groupId, stage }: {
  project: string; episode: number; groupId: string; stage: NarrativeStageState;
}) {
  const qc = useQueryClient();
  const tasks = useTasks({ project, episode });
  const [scope, setScope] = useState<string | null>(null);
  const [settledScope, setSettledScope] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [reason, setReason] = useState("");
  const [selected, setSelected] = useState("");
  const [decision, setDecision] = useState<keyof typeof decisionLabels>("acknowledged");
  const prefix = `group_${groupId}_video_quality_r${stage.revision}_`;
  const task = tasks.data?.data.find((item) => item.task_type === "narrative_group_video_quality"
    && (scope ? item.scope === scope : item.scope?.startsWith(prefix) && activeStatuses.has(item.status)));
  const running = submitting || !!(task && activeStatuses.has(task.status))
    || (!!scope && !task && settledScope !== scope);
  useEffect(() => {
    // Remember a task discovered after remount so its terminal update remains
    // observable when it disappears from the active-task selection.
    if (!scope && task?.scope && activeStatuses.has(task.status)) setScope(task.scope);
  }, [scope, task?.scope, task?.status]);
  useEffect(() => {
    if (!task || activeStatuses.has(task.status)) return;
    setSettledScope(task.scope || null);
    void qc.invalidateQueries({ queryKey: queryKeys.narrativeGroups(project, episode) });
  }, [task?.status, task?.scope, qc, project, episode]);
  const reviewedIssues = (stage.quality_reviews || []).flatMap((review) =>
    review.status === "failed" && review.review_id && review.media_fingerprint && review.evidence?.length
      ? (review.issues || []).filter((issue) => issue.issue_id).map((issue) => ({ review, issue,
        key: `${review.review_id}:${issue.issue_id}` })) : []);
  const issues = [...new Map(reviewedIssues.map((item) => [item.key, item])).values()];
  const check = async () => {
    setSubmitting(true); setError("");
    try {
      const response = await jsonWithBackendError<{ ok: boolean; data: { scope: string } }>(api.post(
        p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/video/quality-check`,
        { json: { revision: stage.revision } }));
      setScope(response.data.scope);
      await tasks.refetch();
    } catch (e) { setError(e instanceof Error ? e.message : "无法启动检查"); }
    finally { setSubmitting(false); }
  };
  const save = async () => {
    const item = issues.find((entry) => entry.key === selected);
    if (!item || !reason.trim()) return;
    setSubmitting(true); setError("");
    try {
      await jsonWithBackendError(api.post(
        p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/video/quality-disposition`,
        { json: { revision: stage.revision, review_id: item.review.review_id,
          issue_id: item.issue.issue_id, media_fingerprint: item.review.media_fingerprint,
          reason: reason.trim(), decision } }));
      await qc.invalidateQueries({ queryKey: queryKeys.narrativeGroups(project, episode) });
      setReason("");
    } catch (e) { setError(e instanceof Error ? e.message : "无法保存处理记录"); }
    finally { setSubmitting(false); }
  };
  return <div className="mt-3 space-y-2 text-xs">
    <Button size="sm" variant="outline" disabled={running} onClick={check}>{running ? "检查中…" : "检查现有视频"}</Button>
    <p className="text-muted-foreground">使用现有视频抽帧检查，不重新生成视频。</p>
    {task && !activeStatuses.has(task.status) ? <p role="status">{task.status === "completed" ? "检查结束，结果已更新" : `检查未完成：${task.error || task.status}`}</p> : null}
    {issues.length ? <div className="space-y-2">
      <label className="block">人工处理问题<select aria-label="人工处理问题" className="ml-2 bg-background" value={selected} onChange={(e) => setSelected(e.target.value)}>
        <option value="">请选择问题</option>{issues.map(({ key, issue }) => <option key={key} value={key}>{issue.dimension}：{issue.description}</option>)}
      </select></label>
      <select aria-label="处理结论" className="bg-background" value={decision} onChange={(e) => setDecision(e.target.value as keyof typeof decisionLabels)}>{Object.entries(decisionLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
      <textarea aria-label="人工处理理由" className="block w-full rounded border bg-background p-2" placeholder="记录核对原始帧后的理由" maxLength={2000} value={reason} onChange={(e) => setReason(e.target.value)} />
      <Button size="sm" variant="outline" disabled={running || !selected || !reason.trim()} onClick={save}>保存人工处理记录</Button>
      <p className="text-muted-foreground">人工记录保留在审计历史中，不替代完整证据检查。</p>
    </div> : null}
    {stage.quality_dispositions?.map((row) => <p key={row.disposition_id}>{decisionLabels[row.decision]}：{row.reason} · {row.created_at}</p>)}
    {error ? <p role="alert" className="text-destructive">{error}</p> : null}
  </div>;
}
