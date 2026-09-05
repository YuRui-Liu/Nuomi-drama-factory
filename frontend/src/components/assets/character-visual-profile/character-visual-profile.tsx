// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface CharacterNarrativeFactView {
  id: string;
  field: string;
  value: string;
  evidence: string;
  sourceSpan: string;
  assertion: "explicit" | "inferred";
  trust: "trusted" | "legacy_untrusted";
}

export interface CharacterVisualIdentityView {
  face?: string;
  hair?: string;
  body?: string;
  signatureFeatures?: string[];
}

export interface CharacterDesignProposalView {
  proposalId: string;
  title: string;
  rationale: string;
  recommended: boolean;
  identityAnchors: string[];
  asymmetryDetail?: string;
  qualityIssues: string[];
}

export interface CharacterVisualProfileProps {
  biography: string;
  facts: CharacterNarrativeFactView[];
  visualProposal: string;
  proposals?: CharacterDesignProposalView[];
  selectedProposalId?: string | null;
  onSelectProposal?: (proposalId: string) => void | Promise<void>;
  isSelectingProposal?: boolean;
  visualBibleStatus?: "draft" | "confirmed" | "superseded";
  onConfirmVisualBible?: () => void | Promise<void>;
  isConfirmingVisualBible?: boolean;
  visualIdentity: CharacterVisualIdentityView;
  outfitsAndStates: string[];
  promptSnapshot?: string;
  className?: string;
}

const identityLabels: Record<"face" | "hair" | "body", string> = {
  face: "面部",
  hair: "发型",
  body: "体态",
};

function ProfileSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-border/70 bg-card/50 p-4">
      <h3 className="mb-3 text-sm font-semibold text-foreground">{title}</h3>
      {children}
    </section>
  );
}

export function CharacterVisualProfile({
  biography,
  facts,
  visualProposal,
  proposals = [],
  selectedProposalId,
  onSelectProposal,
  isSelectingProposal = false,
  visualBibleStatus,
  onConfirmVisualBible,
  isConfirmingVisualBible = false,
  visualIdentity,
  outfitsAndStates,
  promptSnapshot,
  className,
}: CharacterVisualProfileProps) {
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const trustedFacts = facts.filter((fact) => fact.trust === "trusted");
  const quarantinedCount = facts.length - trustedFacts.length;
  const identityEntries = (["face", "hair", "body"] as const).filter(
    (key) => visualIdentity[key]?.trim(),
  );

  return (
    <div className={cn("space-y-3", className)}>
      <ProfileSection title="人物小传">
        <p className="text-sm leading-6 text-muted-foreground">{biography || "暂无人物小传"}</p>
      </ProfileSection>

      <ProfileSection title="剧本明确事实">
        <div className="space-y-2">
          {trustedFacts.length ? (
            trustedFacts.map((fact) => (
              <article key={fact.id} className="rounded-lg bg-muted/40 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">{fact.value}</span>
                  <span className="rounded bg-background px-1.5 py-0.5 text-[11px] text-muted-foreground">
                    {fact.assertion === "explicit" ? "剧本明示" : "推断待确认"}
                  </span>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">依据：{fact.evidence}</p>
                <p className="mt-1 text-[11px] text-muted-foreground/80">来源：{fact.sourceSpan}</p>
              </article>
            ))
          ) : (
            <p className="text-sm text-muted-foreground">剧本中暂无可核验的外观事实。</p>
          )}
          {quarantinedCount > 0 ? (
            <div className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
              <span className="font-medium">不可信旧数据</span>
              <span>已隔离 {quarantinedCount} 条，不参与视觉约束和提示词编译。</span>
            </div>
          ) : null}
        </div>
      </ProfileSection>

      <ProfileSection title="视觉提案">
        {proposals.length ? (
          <div className="grid gap-3 lg:grid-cols-3">
            {proposals.map((proposal) => {
              const selected = proposal.proposalId === selectedProposalId;
              return (
                <button
                  key={proposal.proposalId}
                  type="button"
                  aria-pressed={selected}
                  disabled={isSelectingProposal || visualBibleStatus === "confirmed"}
                  onClick={() => void onSelectProposal?.(proposal.proposalId)}
                  className={cn(
                    "rounded-lg border p-3 text-left transition-colors disabled:cursor-default",
                    selected
                      ? "border-primary/60 bg-primary/10"
                      : "border-border/70 bg-muted/20 hover:border-border hover:bg-muted/35",
                  )}
                >
                  <span className="flex items-start justify-between gap-2">
                    <span className="text-sm font-semibold text-foreground">
                      {proposal.title}
                    </span>
                    {proposal.recommended ? (
                      <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300">
                        推荐
                      </span>
                    ) : null}
                  </span>
                  <span className="mt-2 block text-xs leading-5 text-muted-foreground">
                    {proposal.rationale}
                  </span>
                  {proposal.identityAnchors.length ? (
                    <span className="mt-3 block text-xs text-muted-foreground">
                      <span className="font-medium text-foreground/80">差异锚点</span>
                      <span className="mt-1 block">{proposal.identityAnchors.join("、")}</span>
                    </span>
                  ) : null}
                  {proposal.asymmetryDetail ? (
                    <span className="mt-2 block text-xs text-muted-foreground">
                      <span className="font-medium text-foreground/80">不对称细节：</span>
                      {proposal.asymmetryDetail}
                    </span>
                  ) : null}
                  {proposal.qualityIssues.length ? (
                    <span className="mt-2 block rounded-md border border-amber-500/25 bg-amber-500/5 px-2 py-1.5 text-xs text-amber-700 dark:text-amber-300">
                      {proposal.qualityIssues.join("；")}
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>
        ) : (
          <p className="text-sm leading-6 text-muted-foreground">
            {visualProposal || "尚未生成视觉提案。视觉提案属于创作建议，确认后才会进入身份设定。"}
          </p>
        )}
      </ProfileSection>

      <ProfileSection title="视觉身份设定">
        {visualBibleStatus === "confirmed" ? (
          <div className="mb-3 rounded-lg border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 text-xs font-medium text-emerald-700 dark:text-emerald-300">
            VisualBible 已确认
          </div>
        ) : visualBibleStatus === "draft" && onConfirmVisualBible ? (
          <div className="mb-3 flex items-center justify-between gap-3 rounded-lg border border-amber-500/25 bg-amber-500/5 px-3 py-2">
            <span className="text-xs text-amber-700 dark:text-amber-300">
              当前为草稿，确认后才可生成肖像。
            </span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={isConfirmingVisualBible || !selectedProposalId}
              onClick={() => void onConfirmVisualBible()}
            >
              确认 VisualBible
            </Button>
          </div>
        ) : null}
        {identityEntries.length || visualIdentity.signatureFeatures?.length ? (
          <dl className="grid gap-2 sm:grid-cols-3">
            {identityEntries.map((key) => (
              <div key={key} className="rounded-lg bg-muted/40 p-3">
                <dt className="text-xs text-muted-foreground">{identityLabels[key]}</dt>
                <dd className="mt-1 text-sm">{visualIdentity[key]}</dd>
              </div>
            ))}
            {visualIdentity.signatureFeatures?.length ? (
              <div className="rounded-lg bg-muted/40 p-3 sm:col-span-3">
                <dt className="text-xs text-muted-foreground">标志性特征</dt>
                <dd className="mt-1 text-sm">{visualIdentity.signatureFeatures.join("、")}</dd>
              </div>
            ) : null}
          </dl>
        ) : (
          <p className="text-sm text-muted-foreground">尚未确认视觉身份设定。</p>
        )}
      </ProfileSection>

      <ProfileSection title="服装与状态">
        {outfitsAndStates.length ? (
          <ul className="space-y-2 text-sm">
            {outfitsAndStates.map((item) => (
              <li key={item} className="rounded-lg bg-muted/40 px-3 py-2">
                {item}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">暂无服装或剧情状态设定。</p>
        )}
      </ProfileSection>

      {promptSnapshot ? (
        <div className="rounded-xl border border-dashed border-border/70 p-3">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-expanded={showDiagnostics}
            onClick={() => setShowDiagnostics((value) => !value)}
          >
            高级诊断
          </Button>
          {showDiagnostics ? (
            <div className="mt-3 rounded-lg bg-muted/40 p-3">
              <h4 className="text-xs font-semibold">系统编译提示词</h4>
              <p className="mt-1 text-[11px] text-muted-foreground">只读快照，用于复盘本次生成输入。</p>
              <pre className="mt-2 whitespace-pre-wrap break-words text-xs text-muted-foreground">
                {promptSnapshot}
              </pre>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
