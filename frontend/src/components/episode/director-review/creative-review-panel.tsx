import type { DirectorPlanRevision, ShotPlan } from "@/lib/queries/director-plans";
import { Button } from "@/components/ui/button";

const dimensions: Record<string, string> = {
  optics: "焦段与焦点", framing: "景别", camera_position: "机位", composition: "构图",
  motion: "运镜", performance: "人物动作与表演", lighting: "光影", handoff: "镜头衔接",
};
const labels: Record<string, string> = {
  focal_length_mm: "焦距（毫米）", sensor_basis: "画幅基准", field_of_view_degrees: "视场角",
  field_of_view_axis: "视场方向", subject_distance: "主体距离", depth_of_field: "景深",
  focus_target: "焦点对象", focus_change: "焦点变化", start_frame: "开始取景", end_frame: "结束取景",
  must_read: "必须可读", landmark: "参照地标", position: "位置", height: "高度", distance: "距离",
  direction: "朝向", axis_side: "轴线侧", attention_center: "注意中心", start_composition: "起始构图",
  end_composition: "结束构图", subject_placement: "主体位置", frame_occupancy: "画面占比",
  depth_layers: "纵深", occlusion: "遮挡", look_room: "视线空间", trigger: "触发节拍", path: "路径",
  speed_profile: "速度", end_condition: "结束条件", constraints: "约束", characters: "人物",
  subject_id: "人物", goal: "目标", obstacle: "阻力", tactics: "策略", visible_behavior: "可见行为",
  gaze: "视线", contact: "接触", held_objects: "持物", weight_and_inertia: "重量与惯性", beats: "节拍",
  light_ids: "光源", camera_relative_lighting: "当前机位受光", shadow: "阴影", quality: "光质",
  color: "颜色", subject_background_exposure: "主体与背景曝光", eye_light: "眼神光",
  next_shot_id: "下一镜", cut_trigger: "切点", spatial_handoff: "空间交接", gaze_handoff: "视线交接",
  action_phase: "动作相位", held_object_handoff: "持物交接", emotional_handoff: "情绪延续",
  sound_bridge: "声音衔接", intentional_discontinuity: "有意不连续", reason: "原因",
  applicable: "适用", mode: "方式", end_focus_target: "末帧焦点",
};
const statuses: Record<string, string> = {
  unavailable: "本轮审核或修订未完成",
  passed: "创作复查通过", needs_revision: "创作复查需修改", unreviewed: "等待独立 QC",
  needs_human: "需要人工判断", resolved: "已由 QC 确认解决", persists: "问题仍存在",
  regressed: "问题回归", proposed_change: "作者提出修改", disputed: "作者有异议", blocked: "作者遇到阻塞",
};

function describe(value: unknown): string {
  if (value == null) return "—";
  if (Array.isArray(value)) return value.map(describe).join("；");
  if (typeof value === "object") return Object.entries(value).filter(([key]) => key !== "source" && key !== "purpose")
    .map(([key, item]) => `${labels[key] ?? key}：${describe(item)}`).join("；");
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value);
}

export function ShotCreativeDetails({ shot, open = false }: { shot: ShotPlan; open?: boolean }) {
  return <details open={open} className="min-w-0 break-words rounded-lg border p-3">
    <summary className="cursor-pointer font-medium">导演细节 · 镜头 {shot.id}</summary>
    <dl className="mt-3 grid gap-4 sm:grid-cols-2">{Object.entries(dimensions).map(([key, title]) => {
      const decision = shot.creative?.[key] as Record<string, unknown> | undefined;
      return <div key={key} className="min-w-0"><dt className="font-semibold">{title}</dt>
        <dd>{typeof decision?.purpose === "string" ? decision.purpose : "缺少创作决定"}</dd>
        {decision && <dd className="text-muted-foreground">{describe(decision)}</dd>}
        {decision?.source != null && <dd className="text-muted-foreground">依据：{describe(decision.source)}</dd>}
      </div>;
    })}</dl>
    <p className="mt-3">原有镜头参数：{shot.shot_size} · {shot.camera_angle} · {shot.composition} · {shot.camera_motion}</p>
    <p>可见起始状态：{shot.visible_start_state}</p><p>可见结束状态：{shot.visible_end_state}</p>
    {shot.intent && <p>情绪效果：{shot.intent.emotional_effect}；连续性策略：{shot.intent.continuity_strategy}</p>}
    <p>原文来源：{shot.source_span_ids.join("；") || "—"}</p>
    <p>对白来源：{shot.dialogue_source_ids.join("；") || "—"}</p>
    {shot.asset_requirements?.map((asset, index) => <p key={index}>素材详情：{asset.kind} · {asset.entity_key} · {asset.design_notes} · {asset.visible_change} · {asset.required ? "必需" : "可选"} · 依据：{asset.evidence_source_ids.join("；")}</p>)}
    {shot.creative && <details className="mt-2"><summary>完整导演原始字段</summary><pre className="whitespace-pre-wrap break-all">{JSON.stringify(shot.creative, null, 2)}</pre></details>}
  </details>;
}

export function CreativeReviewPanel({ revision, showShotDetails = true, onLocateShot }: { revision: DirectorPlanRevision; showShotDetails?: boolean; onLocateShot?: (shotId: string) => void }) {
  if (revision.creative_contract_version !== 1) {
    return <p className="text-sm text-muted-foreground">旧版本：尚未按创作标准审查</p>;
  }
  const feedback = revision.creative_feedback;
  const latestAssessments = feedback?.qc_rounds[feedback.qc_rounds.length - 1]?.assessments;
  return <section aria-label="导演创作决策与复查" className="min-w-0 space-y-4 break-words rounded-xl border p-4">
    <h3 className="font-semibold">{statuses[revision.creative_review_status ?? "unreviewed"] ?? "等待独立 QC"}</h3>
    {revision.validation_report.issues.filter((issue) => ["creative_qc_unavailable", "creative_repair_invalid"].includes(issue.code))
      .map((issue) => <p key={issue.code} role="alert" className="text-sm text-destructive">本轮审核或修订未完成：{issue.message}。下方保留的是历史结论，不代表本轮重新确认。</p>)}
    {showShotDetails && revision.groups.flatMap((group) => group.shots.map((shot) => <ShotCreativeDetails key={shot.id} shot={shot} open />))}
    {latestAssessments?.length ? <details>
      <summary className="text-sm font-medium">最近一轮逐镜检查证据</summary>
      {latestAssessments.map((item) => <div key={`${item.shot_id}:${item.dimension}`} className="mt-2 text-xs">
        <strong>{item.shot_id} · {dimensions[item.dimension] ?? item.dimension} · {statuses[item.conclusion] ?? item.conclusion}</strong>
        <p>{item.reason}</p><p className="text-muted-foreground">{item.evidence_paths.join("；")}</p>
        {onLocateShot && revision.groups.some((group) => group.shots.some((shot) => shot.id === item.shot_id)) && <Button size="sm" variant="outline" onClick={() => onLocateShot(item.shot_id)}>定位镜头 {item.shot_id}</Button>}
      </div>)}
    </details> : null}
    {feedback?.issues.map(({ issue, status }) => <article key={issue.issue_id} className="space-y-2 border-t pt-3">
      <h4 className="font-medium">{issue.issue_id} · {issue.description}</h4>
      <p className="whitespace-pre-wrap text-sm">最新复查理由：{[...feedback.qc_rounds].reverse()
        .flatMap((round) => round.dispositions).find((item) => item.issue_id === issue.issue_id)?.explanation ?? issue.description}</p>
      <p className="text-sm">{statuses[status] ?? status} · 验收条件：{issue.acceptance_criteria}</p>
      <details><summary className="cursor-pointer text-sm">作者与 QC 历史记录</summary>
      {feedback.author_rounds.flatMap((round) => round.responses.filter((item) => item.issue_id === issue.issue_id)
        .map((item) => <div key={`a:${round.round_id}`}><strong className="text-xs">作者修改说明 · {statuses[item.status] ?? item.status}</strong><p className="text-sm">{item.explanation}</p></div>))}
      {feedback.qc_rounds.flatMap((round) => round.dispositions.filter((item) => item.issue_id === issue.issue_id)
        .map((item) => <div key={`q:${round.round_id}`}><strong className="text-xs">独立 QC 复查 · {statuses[item.status] ?? item.status}</strong><p className="whitespace-pre-wrap text-sm">{item.explanation}</p></div>))}
      </details>
    </article>)}
    {feedback && <details><summary className="cursor-pointer text-sm font-medium">完整审核历史与原始证据</summary><pre className="mt-2 whitespace-pre-wrap break-all text-sm">{JSON.stringify(feedback, null, 2)}</pre></details>}
  </section>;
}
