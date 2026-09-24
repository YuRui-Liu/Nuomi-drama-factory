import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  castingTaskActive,
  castingTaskStatus,
  useCharacterCasting,
  useCastingAction,
  useCastingCandidates,
} from "@/lib/queries/character-casting";
import { useGenerationCreditCost } from "@/lib/queries/generation-credit-cost";
import { resolveMediaUrl } from "@/lib/media-url";
import type {
  CastingAction,
  CastingCandidate,
  CastingFact,
  CastingWorkspace,
} from "@/types/character-casting";

type Props = { project: string; name: string; imageModel?: string };
type RunAction = (action: CastingAction, onSuccess?: () => void) => void;
const card = "rounded-xl border border-border/70 bg-card/50 p-4 space-y-3";
const field =
  "w-full rounded-md border border-border bg-background px-3 py-2 text-sm disabled:opacity-60";
const dimensionLabels = {
  facts: "原文符合度",
  design: "设计执行度",
  distinctiveness: "角色辨识度",
};
const verdictLabels = {
  conforms: "符合",
  deviation: "存在差异",
  unjudgeable: "无法判断",
};
const visibilityLabels = {
  visible: "可见",
  not_visible: "不可见",
  uncertain: "可见性不确定",
};
const warningLabels: Record<string, string> = {
  review_failed: "检查失败，结果未知",
  review_not_started: "尚未检查，结果未知",
  review_unjudgeable: "没有可判断的检查结果",
};
const attributeLabels: Record<string, string> = {
  face_shape: "脸型",
  hair_style: "发型",
  body_type: "体态",
  facial_feature: "五官特征",
  facial_features: "五官特征",
  distinctive_feature: "辨识特征",
  distinctive_features: "辨识特征",
  identity_anchors: "辨识锚点",
  asymmetry_detail: "不对称细节",
  age_group: "年龄阶段",
  age_range: "年龄范围",
  gender: "性别",
  species: "物种",
  grooming: "仪容",
  maintenance: "打理程度",
  posture: "姿态",
  clothing_state: "服装状态",
  presentation: "整体呈现",
  face: "面部",
  build: "体格",
  appearance: "外观",
  outfit: "服装",
};
function friendly(value: string) {
  if (
    value.includes("stale") ||
    value.includes("revision conflict") ||
    value.includes("source/style changed")
  )
    return "依据或设计版本已变化，请刷新并重新选角。";
  if (value.includes("review_running")) return "检查进行中，请等待结果。";
  if (value.includes("generation_not_succeeded")) return "候选尚未生成成功。";
  if (value.includes("missing:visual_evidence"))
    return "原文尚无可核验的外观事实，设计中的补充会标为自由选择。";
  if (value.includes("identity_required"))
    return "部分事实属于独立身份阶段，请切换阶段查看。";
  if (value.includes("untrusted") || value.includes("unverified_evidence"))
    return "部分资料缺少可信原文依据，已排除出硬约束。";
  if (value.includes("conflicting")) return "原文事实存在冲突，请先核对来源。";
  return value;
}

function Facts({ facts }: { facts: CastingFact[] }) {
  return (
    <div className="space-y-2">
      {facts.map((f) => (
        <article key={f.fact_id} className="rounded-lg bg-muted/30 p-3 text-sm">
          <p>{f.value}</p>
          <details className="mt-2 text-xs text-muted-foreground">
            <summary className="cursor-pointer">查看来源原文</summary>
            <blockquote className="mt-2 whitespace-pre-wrap">
              {f.evidence}
            </blockquote>
            <p className="mt-1">
              {f.source_document || "剧本"} · {f.source_span.start_line}–
              {f.source_span.end_line} 行
            </p>
          </details>
        </article>
      ))}
    </div>
  );
}

export function CharacterCastingPanel({
  project,
  name,
  imageModel,
  identityId: controlledIdentityId,
  onIdentityChange,
}: Props & {
  identityId?: string | null;
  onIdentityChange?: (identityId: string | null) => void;
}) {
  const [localIdentityId, setLocalIdentityId] = useState<string | null>(null);
  const identityId =
    controlledIdentityId === undefined ? localIdentityId : controlledIdentityId;
  const setIdentityId = (next: string | null) => {
    setLocalIdentityId(next);
    onIdentityChange?.(next);
  };
  const base = useCharacterCasting(project, name);
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">剧情驱动选角</h2>
        <label className="flex items-center gap-2 text-sm">
          身份阶段
          <select
            className={field}
            aria-label="身份阶段"
            value={identityId ?? ""}
            onChange={(e) => setIdentityId(e.target.value || null)}
          >
            <option value="">基础形象</option>
            {base.data?.data.identities.map((i) => (
              <option key={i.identity_id} value={i.identity_id}>
                {i.name || i.identity_id}
              </option>
            ))}
          </select>
        </label>
      </div>
      <CastingStage
        key={`${project}/${name}/${identityId ?? "base"}`}
        project={project}
        name={name}
        imageModel={imageModel}
        identityId={identityId}
      />
    </div>
  );
}

function CastingStage({
  project,
  name,
  imageModel,
  identityId,
}: Props & { identityId: string | null }) {
  const workspace = useCharacterCasting(project, name, identityId);
  const candidates = useCastingCandidates(project, name, identityId);
  const mutation = useCastingAction(project, name, identityId);
  const cost = useGenerationCreditCost("image_selection", imageModel, {
    surface: "supertale",
    imageRole: "character",
  });
  const [selectedCandidate, setSelectedCandidate] = useState<string | null>(
    null,
  );
  const [submitted, setSubmitted] = useState<CastingAction | null>(null);
  const retrySuccess = useRef<(() => void) | undefined>(undefined);
  const [notice, setNotice] = useState("");
  const data = workspace.data?.data;
  // Submission polling also refreshes candidates, including a review which has
  // been enqueued but has not yet changed the candidate's durable state.
  useEffect(() => {
    if (data?.tasks.length) void candidates.refetch();
  }, [workspace.dataUpdatedAt]); // eslint-disable-line react-hooks/exhaustive-deps
  const run: RunAction = (action, onSuccess) => {
    if (data?.can_edit !== true || mutation.isPending) return;
    retrySuccess.current = onSuccess;
    setSubmitted(action);
    setNotice("");
    mutation.mutate(action, {
      onSuccess: () => {
        onSuccess?.();
        setSubmitted(null);
        setNotice(
          action.kind === "adopt"
            ? "定角已确认，当前形象已更新。"
            : action.kind === "draft"
              ? "设计已保存，可生成候选。"
              : "任务已提交，可离开后返回查看。",
        );
      },
    });
  };
  const busy = mutation.isPending || mutation.isError;
  if (workspace.isLoading)
    return <p className="text-sm text-muted-foreground">正在读取选角资料…</p>;
  if (!data)
    return (
      <div role="alert" className={card}>
        <p>选角资料读取失败，请重试。</p>
        <Button variant="outline" onClick={() => void workspace.refetch()}>
          重新读取
        </Button>
      </div>
    );
  const writable = data.can_edit === true;
  const candidate = candidates.data?.data.find(
    (c) => c.candidate_id === selectedCandidate,
  );
  const tasksRunning = data.tasks.some(castingTaskActive);
  const current = data.current ?? data.legacy_current;
  return (
    <div className="space-y-5">
      {!writable && (
        <p className="text-sm text-muted-foreground">
          当前为只读权限，可查看依据、候选和检查结果。
        </p>
      )}
      {workspace.isError && (
        <div role="alert">
          资料刷新失败。
          <Button variant="outline" onClick={() => void workspace.refetch()}>
            重新读取
          </Button>
        </div>
      )}
      <section className={card} aria-label="选角依据">
        <h3 className="font-semibold">选角依据</h3>
        <p className="text-sm leading-6 text-muted-foreground">
          {data.dossier.narrative.biography || "尚无人物小传"}
        </p>
        <h4 className="text-sm font-medium">原文事实</h4>
        <Facts facts={data.dossier.hard_constraints} />
        {!data.dossier.hard_constraints.length && (
          <p className="text-sm text-muted-foreground">
            暂无可核验的外观硬约束。
          </p>
        )}
        {data.dossier.interpretations.length > 0 && (
          <>
            <h4 className="text-sm font-medium">人物背景依据</h4>
            <Facts facts={data.dossier.interpretations} />
          </>
        )}
        {data.dossier.issues.map((issue, i) => (
          <p key={i} className="text-xs text-amber-500">
            {friendly(issue)}
          </p>
        ))}
        {data.prerequisite_error && (
          <p role="alert" className="text-sm text-amber-500">
            缺少可用的原文来源，请先在导入页保存剧本。
            {friendly(data.prerequisite_error)}
          </p>
        )}
        {data.limitation_reason && (
          <p role="alert" className="text-sm text-amber-500">
            选角依据受限：{friendly(data.limitation_reason)}
          </p>
        )}
        {data.draft_stale && (
          <p role="alert" className="text-sm text-amber-500">
            选角草稿已过期，请基于最新原文和风格重新选角。
          </p>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="outline"
            disabled={
              !writable || busy || tasksRunning || !!data.prerequisite_error
            }
            onClick={() =>
              run({
                kind: "recast",
                body: {
                  expected_revision: data.revision?.revision_id ?? null,
                  idempotency_key: crypto.randomUUID(),
                },
              })
            }
          >
            重新选角
          </Button>
          <span className="text-xs text-muted-foreground">
            重新整理三套方案，保留当前形象。文本模型使用项目配置，费用按实际调用计。
          </span>
        </div>
      </section>
      <ProposalEditor
        key={data.revision?.revision_id ?? "empty"}
        data={data}
        writable={writable && !busy && !tasksRunning}
        run={run}
        imageModel={imageModel}
        cost={cost.data?.data.display}
      />
      <section className={card}>
        <h3 className="font-semibold">候选与检查</h3>
        <div className="rounded-lg bg-muted/20 p-3">
          <h4 className="mb-2 text-sm font-medium">当前定角</h4>
          {current?.url ? (
            <img
              src={resolveMediaUrl(current.url) ?? current.url}
              alt="当前定角"
              className="max-h-64 rounded-lg object-contain"
            />
          ) : (
            <p className="text-sm text-muted-foreground">尚未定角</p>
          )}
          {data.current && !data.current.candidate_id && (
            <p className="mt-2 text-xs text-muted-foreground">
              历史定角，尚未经过本次剧情选角流程。
            </p>
          )}
          {data.legacy_current && (
            <p className="mt-2 text-xs text-amber-500">
              历史形象尚未经过本次选角确认，继续保留。
            </p>
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          生成只增加候选；确认定角后才会更新当前形象。
        </p>
        {candidates.isLoading && <p>正在读取候选…</p>}
        {candidates.isError && (
          <div role="alert">
            候选读取失败。
            <Button variant="outline" onClick={() => void candidates.refetch()}>
              重新读取候选
            </Button>
          </div>
        )}
        <div className="grid gap-4 xl:grid-cols-2">
          {candidates.data?.data.map((c, i) => (
            <CandidateCard
              key={c.candidate_id}
              candidate={c}
              index={i + 1}
              selected={selectedCandidate === c.candidate_id}
              disabled={!writable || busy}
              onSelect={() => setSelectedCandidate(c.candidate_id)}
              onReview={() =>
                run({
                  kind: "review",
                  candidateId: c.candidate_id,
                  body: { idempotency_key: crypto.randomUUID() },
                })
              }
            />
          ))}
        </div>
        {candidates.data?.data.length === 0 && (
          <p className="text-sm text-muted-foreground">
            尚无候选。选择设计方案后生成第一张候选。
          </p>
        )}
      </section>
      <section role="region" aria-label="确认定角" className={card}>
        <h3 className="font-semibold">确认定角</h3>
        {candidate ? (
          <AdoptionForm
            key={`${candidate.candidate_id}/${candidate.adoption_requirements.expected_review_attempt_id}/${JSON.stringify(candidate.adoption_requirements)}/${data.revision?.revision_id}`}
            candidate={candidate}
            disabled={
              !writable ||
              busy ||
              data.draft_stale ||
              !!data.prerequisite_error ||
              candidates.isError ||
              workspace.isError
            }
            revision={data.revision?.revision_id}
            run={run}
          />
        ) : (
          <p className="text-sm text-muted-foreground">
            先选择一张候选，再核对检查结果并确认。
          </p>
        )}
      </section>
      {data.tasks.length > 0 && (
        <div
          className="space-y-2 text-xs text-muted-foreground"
          aria-label="选角任务"
        >
          {data.tasks.map((t) => (
            <p key={t.request_id}>
              {t.operation === "recast"
                ? "方案整理"
                : t.operation === "review"
                  ? "候选检查"
                  : "候选生成"}
              ：
              {castingTaskActive(t)
                ? "进行中，可稍后返回"
                : castingTaskStatus(t) === "failed"
                  ? "失败"
                  : ["completed", "succeeded"].includes(castingTaskStatus(t))
                    ? "已完成"
                    : castingTaskStatus(t)}
              {t.error ? ` · ${friendly(t.error)}` : ""}
            </p>
          ))}
        </div>
      )}
      {mutation.isError && (
        <div role="alert" className={card}>
          <p className="text-sm">
            操作未确认成功：{friendly(mutation.error.message)}
          </p>
          <p className="text-xs text-muted-foreground">
            若网络中断，可重试同一次请求，系统会避免重复提交。
          </p>
          <div className="flex gap-2">
            <Button
              disabled={!writable || mutation.isPending || !submitted}
              onClick={() => submitted && run(submitted, retrySuccess.current)}
            >
              重试同一次请求
            </Button>
            <Button
              variant="outline"
              onClick={() => {
                mutation.reset();
                setSubmitted(null);
                retrySuccess.current = undefined;
                void workspace.refetch();
                void candidates.refetch();
              }}
            >
              刷新状态
            </Button>
          </div>
        </div>
      )}
      {notice && (
        <p role="status" className="text-sm text-lime-500">
          {notice}
        </p>
      )}
    </div>
  );
}

function ProposalEditor({
  data,
  writable,
  run,
  imageModel,
  cost,
}: {
  data: CastingWorkspace;
  writable: boolean;
  run: RunAction;
  imageModel?: string;
  cost?: string;
}) {
  const [proposals, setProposals] = useState(data.proposals);
  const [selected, setSelected] = useState(data.selected_proposal_id);
  const signature = JSON.stringify({ selected, proposals });
  const [savedSignature, setSavedSignature] = useState(signature);
  const dirty = signature !== savedSignature;
  const canEdit =
    writable &&
    !!data.revision &&
    !data.draft_stale &&
    !data.prerequisite_error;
  const invalidSet =
    proposals.length !== 3 ||
    new Set(proposals.map((p) => p.proposal_id)).size !== 3 ||
    proposals.filter((p) => p.recommended).length !== 1;
  const selectedProposal = proposals.find((p) => p.proposal_id === selected);
  function updateDecision(
    proposalId: string,
    decisionId: string,
    key: "value" | "reason",
    value: string,
  ) {
    setProposals((items) =>
      items.map((p) => {
        if (p.proposal_id !== proposalId) return p;
        const decision = p.casting_decisions.find(
          (d) => d.decision_id === decisionId,
        );
        const updated = {
          ...p,
          casting_decisions: p.casting_decisions.map((d) =>
            d.decision_id !== decisionId ? d : { ...d, [key]: value },
          ),
        };
        if (key === "value" && decision) {
          const original =
            data.proposals.find((item) => item.proposal_id === proposalId) ?? p;
          const originalValue =
            original.casting_decisions.find(
              (item) => item.decision_id === decisionId,
            )?.value ?? decision.value;
          // Keep the actual rendered fields in the immutable snapshot consistent
          // with the edited decision; evidence and its references stay untouched.
          for (const scalar of [
            "face_shape",
            "hair_style",
            "body_type",
            "asymmetry_detail",
          ] as const) {
            if (
              decision.attribute === scalar ||
              (originalValue.trim() && original[scalar] === originalValue)
            )
              updated[scalar] = value;
          }
          for (const list of [
            "facial_features",
            "distinctive_features",
            "identity_anchors",
          ] as const) {
            updated[list] = p[list]?.map((item, index) =>
              originalValue.trim() && original[list]?.[index] === originalValue
                ? value
                : item,
            );
          }
          updated.outfit_states = Object.fromEntries(
            Object.entries(p.outfit_states ?? {}).map(([state, text]) => [
              state,
              originalValue.trim() &&
              original.outfit_states?.[state] === originalValue
                ? value
                : text,
            ]),
          );
        }
        return updated;
      }),
    );
  }
  const blanks = selectedProposal?.casting_decisions.some(
    (d) => !d.value.trim() || !d.reason.trim(),
  );
  return (
    <section className={card}>
      <h3 className="font-semibold">三套选角方案</h3>
      <p className="text-sm text-muted-foreground">
        设计解释说明如何使用依据；自由选择是创作补充，不会变成原文事实。
      </p>
      <div className="grid gap-3 lg:grid-cols-3">
        {proposals.map((proposal) => (
          <article
            key={proposal.proposal_id}
            className={`space-y-3 rounded-lg border p-4 ${selected === proposal.proposal_id ? "border-lime-500/60 bg-lime-500/5" : "border-border bg-muted/20"}`}
          >
            <div className="flex items-center justify-between gap-2">
              <h4 className="text-sm font-semibold">{proposal.title}</h4>
              {proposal.recommended && (
                <span className="text-xs text-lime-500">推荐</span>
              )}
            </div>
            <p className="text-xs leading-5 text-muted-foreground">
              {proposal.rationale}
            </p>
            {proposal.casting_decisions.map((d) => (
              <div
                key={d.decision_id}
                className="space-y-1 border-t border-border/50 pt-2 text-xs"
              >
                <p className="font-medium">
                  {d.basis === "evidence" ? "设计解释" : "自由选择"}
                </p>
                <p>
                  {attributeLabels[d.attribute] ?? d.attribute}：{d.value}
                </p>
                <p className="text-muted-foreground">{d.reason}</p>
                {d.fact_ids.map((id) => (
                  <p key={id} className="text-muted-foreground">
                    原文依据：
                    {[
                      ...data.dossier.hard_constraints,
                      ...data.dossier.interpretations,
                    ].find((f) => f.fact_id === id)?.value ?? id}
                  </p>
                ))}
              </div>
            ))}
            {proposal.identity_anchors?.length ? (
              <p className="text-xs text-muted-foreground">
                辨识特征：{proposal.identity_anchors.join("、")}
              </p>
            ) : null}
            {proposal.quality_issues?.map((issue) => (
              <p key={issue} className="text-xs text-amber-500">
                {friendly(issue)}
              </p>
            ))}
            <Button
              variant="outline"
              size="sm"
              aria-pressed={selected === proposal.proposal_id}
              disabled={
                !canEdit || invalidSet || !!proposal.quality_issues?.length
              }
              onClick={() => {
                setSelected(proposal.proposal_id);
              }}
            >
              选择{proposal.title}
            </Button>
            {selected === proposal.proposal_id && (
              <details>
                <summary className="cursor-pointer text-xs">
                  编辑造型决定
                </summary>
                <div className="mt-3 space-y-3">
                  {proposal.casting_decisions.map((d) => (
                    <div key={d.decision_id} className="space-y-2">
                      <label className="block text-xs">
                        {attributeLabels[d.attribute] ?? d.attribute}造型决定
                        <input
                          className={field}
                          disabled={!canEdit}
                          value={d.value}
                          onChange={(e) =>
                            updateDecision(
                              proposal.proposal_id,
                              d.decision_id,
                              "value",
                              e.target.value,
                            )
                          }
                        />
                      </label>
                      <label className="block text-xs">
                        设计理由
                        <textarea
                          className={field}
                          disabled={!canEdit}
                          value={d.reason}
                          onChange={(e) =>
                            updateDecision(
                              proposal.proposal_id,
                              d.decision_id,
                              "reason",
                              e.target.value,
                            )
                          }
                        />
                      </label>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </article>
        ))}
      </div>
      {!proposals.length && (
        <p className="text-sm text-muted-foreground">
          点击“重新选角”，根据当前剧情整理三套方案。
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="outline"
          disabled={
            !canEdit ||
            !dirty ||
            !selected ||
            invalidSet ||
            blanks ||
            !!selectedProposal?.quality_issues?.length
          }
          onClick={() =>
            selected &&
            data.revision &&
            run(
              {
                kind: "draft",
                body: {
                  expected_revision: data.revision.revision_id,
                  selected_proposal_id: selected,
                  proposals,
                },
              },
              // Capture the submitted payload, not whatever is being edited
              // when this response arrives. Newer edits remain dirty.
              () => setSavedSignature(signature),
            )
          }
        >
          保存选角方案
        </Button>
        <Button
          className="bg-lime-400 text-black hover:bg-lime-300"
          disabled={
            !canEdit ||
            dirty ||
            !selected ||
            invalidSet ||
            !!selectedProposal?.quality_issues?.length
          }
          onClick={() =>
            data.revision &&
            run({
              kind: "generate",
              body: {
                expected_revision: data.revision.revision_id,
                idempotency_key: crypto.randomUUID(),
                ...(imageModel ? { model: imageModel } : {}),
              },
            })
          }
        >
          生成候选
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        模型：{imageModel || "项目默认图像模型"} · 预计费用：
        {cost || "暂无法获取，以实际调用为准"}
        {dirty ? " · 请先保存方案" : ""}
      </p>
    </section>
  );
}

function CandidateCard({
  candidate: c,
  index,
  selected,
  disabled,
  onSelect,
  onReview,
}: {
  candidate: CastingCandidate;
  index: number;
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
  onReview: () => void;
}) {
  const [diagnostics, setDiagnostics] = useState(false);
  const report = c.review_status === "completed" ? c.report : null;
  return (
    <article
      className={`space-y-3 rounded-lg border p-4 ${selected ? "border-lime-500/60" : "border-border"}`}
    >
      <h4 className="text-sm font-semibold">候选 {index}</h4>
      {c.url && (
        <img
          src={resolveMediaUrl(c.url) ?? c.url}
          alt={`候选 ${index}`}
          className="max-h-80 w-full rounded-lg object-contain"
        />
      )}
      <p className="text-sm">
        {c.generation_status === "failed"
          ? "生成失败"
          : c.generation_status === "queued"
            ? "等待生成"
            : c.generation_status === "running"
              ? "生成中"
              : "生成完成"}
      </p>
      {c.stale && (
        <p className="text-sm text-amber-500">
          候选已过期，不能采用。请使用当前方案生成新候选。
        </p>
      )}
      {c.asset_error && (
        <p role="alert" className="text-sm text-amber-500">
          候选图片不可用，请生成新候选。
        </p>
      )}
      <p className="text-sm">
        {c.review_status === "failed"
          ? "检查失败"
          : c.review_status === "running"
            ? "检查中"
            : c.review_status === "not_started"
              ? "尚未检查"
              : "检查已完成"}
      </p>
      {c.error && <p className="text-xs text-amber-500">{c.error}</p>}
      {report && (
        <div className="space-y-3">
          {(["facts", "design", "distinctiveness"] as const).map(
            (dimension) => (
              <div key={dimension}>
                <h5 className="text-xs font-semibold">
                  {dimensionLabels[dimension]}
                </h5>
                {report.findings
                  .filter((f) => f.dimension === dimension)
                  .map((f) => (
                    <div
                      key={f.finding_id}
                      className="mt-2 space-y-1 rounded bg-muted/30 p-2 text-xs"
                    >
                      <p>
                        {verdictLabels[f.verdict]} ·{" "}
                        {visibilityLabels[f.visibility]}
                      </p>
                      <p>{f.description}</p>
                      {f.fact_ids.map((id) => {
                        const fact = c.snapshot.hard_constraints.find(
                          (fact) => fact.fact_id === id,
                        );
                        return (
                          <p key={id}>
                            原文引用：
                            {fact ? `${fact.value}（${fact.evidence}）` : id}
                          </p>
                        );
                      })}
                      {f.decision_ids.map((id) => (
                        <p key={id}>
                          设计引用：
                          {c.snapshot.design_decisions.find(
                            (d) => d.decision_id === id,
                          )?.value ?? id}
                        </p>
                      ))}
                      {f.reference_candidate_ids.length > 0 && (
                        <p>对比版本：{f.reference_candidate_ids.join("、")}</p>
                      )}
                    </div>
                  ))}
                {!report.findings.some((f) => f.dimension === dimension) && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    暂无可判断结果
                  </p>
                )}
              </div>
            ),
          )}
          {report.comparison_scope === "none" && (
            <p className="text-xs text-amber-500">
              未提供其他已定角角色作为对比，无法据此确认跨角色辨识度。
            </p>
          )}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={
            disabled ||
            c.generation_status !== "succeeded" ||
            c.review_status === "running" ||
            !!c.asset_error ||
            !c.url
          }
          onClick={onReview}
        >
          {c.review_status === "failed"
            ? "重试检查"
            : c.review_status === "completed"
              ? "重新检查"
              : "检查候选"}
        </Button>
        <Button
          size="sm"
          variant="outline"
          aria-pressed={selected}
          disabled={
            disabled ||
            c.stale ||
            !!c.adoption_requirements.blocked_reason ||
            !!c.asset_error ||
            !c.url
          }
          onClick={onSelect}
        >
          选择候选 {index}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        检查模型使用项目配置，预计费用按实际检查调用计；重试检查不会重新绘图。
      </p>
      <Button
        variant="ghost"
        size="sm"
        aria-expanded={diagnostics}
        onClick={() => setDiagnostics(!diagnostics)}
      >
        高级诊断
      </Button>
      {diagnostics && (
        <div className="space-y-2 text-xs text-muted-foreground">
          <p>候选版本：{c.candidate_id}</p>
          <p>
            生成模型：
            {c.generation_metadata?.resolved_model ||
              c.requested_model ||
              "未记录"}
          </p>
          <pre className="whitespace-pre-wrap break-words">
            {c.snapshot.prompt}
          </pre>
          {c.review_attempts.map((a) => (
            <p key={a.attempt_id}>
              检查 {a.attempt_id}：{a.status}
              {a.error ? ` · ${a.error}` : ""}
            </p>
          ))}
        </div>
      )}
    </article>
  );
}

function AdoptionForm({
  candidate,
  disabled,
  revision,
  run,
}: {
  candidate: CastingCandidate;
  disabled: boolean;
  revision?: string;
  run: (a: CastingAction) => void;
}) {
  const [acknowledgements, setAcknowledgements] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const requirements = candidate.adoption_requirements;
  const blocked =
    disabled ||
    !revision ||
    candidate.stale ||
    !!requirements.blocked_reason ||
    !!candidate.asset_error ||
    !candidate.url;
  const satisfied =
    requirements.required_acknowledgements.every((id) =>
      acknowledgements.includes(id),
    ) &&
    (!requirements.override_reason_required || !!reason.trim());
  return (
    <div className="space-y-3">
      <p className="text-sm">
        确认后，此候选与对应造型设定将成为当前定角，旧版本保留在历史中。
      </p>
      {requirements.blocked_reason && (
        <p className="text-sm text-amber-500">
          {friendly(requirements.blocked_reason)}
        </p>
      )}
      {requirements.required_acknowledgements.map((id) => (
        <label key={id} className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-1 accent-lime-500"
            disabled={blocked}
            checked={acknowledgements.includes(id)}
            onChange={(e) =>
              setAcknowledgements((old) =>
                e.target.checked
                  ? [...old, id]
                  : old.filter((item) => item !== id),
              )
            }
          />
          <span>
            我已核对：
            {candidate.report?.findings.find((f) => f.finding_id === id)
              ?.description ??
              warningLabels[id] ??
              id}
          </span>
        </label>
      ))}
      {requirements.override_reason_required && (
        <label className="block space-y-2 text-sm">
          <span>采用原因</span>
          <textarea
            className={field}
            disabled={blocked}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="说明人工核对情况，以及仍采用此候选的原因"
          />
        </label>
      )}
      <Button
        disabled={blocked || !satisfied}
        onClick={() =>
          revision &&
          run({
            kind: "adopt",
            candidateId: candidate.candidate_id,
            body: {
              candidate_id: candidate.candidate_id,
              expected_revision: revision,
              expected_review_attempt_id:
                requirements.expected_review_attempt_id,
              idempotency_key: crypto.randomUUID(),
              acknowledged_findings: acknowledgements,
              override_reason: reason.trim() || null,
            },
          })
        }
      >
        确认定角
      </Button>
    </div>
  );
}
