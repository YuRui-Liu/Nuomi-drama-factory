import type { ReactNode } from "react";

export type CharacterFactTrust = "explicit" | "inferred" | "legacy_untrusted";

export interface CharacterFact {
  id?: string;
  label: string;
  value: string;
  sourceLine?: string | number;
  evidence?: string;
  trust: CharacterFactTrust;
}

export interface CharacterVisualProfileProps {
  biography?: string;
  facts?: CharacterFact[];
  visualProposal?: ReactNode;
  visualIdentity?: ReactNode;
  outfitsAndStates?: ReactNode;
  promptSnapshot?: string;
  advancedDiagnostics?: boolean;
  className?: string;
}

const trustLabels: Record<CharacterFactTrust, string> = {
  explicit: "明确事实",
  inferred: "推断",
  legacy_untrusted: "不可信旧数据",
};

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-base font-semibold text-slate-900">{title}</h2>
      <div className="mt-3 text-sm leading-6 text-slate-700">{children}</div>
    </section>
  );
}

function EmptyValue() {
  return <p className="text-slate-400">暂未填写</p>;
}

function FactItem({ fact }: { fact: CharacterFact }) {
  const untrusted = fact.trust === "legacy_untrusted";
  return (
    <li
      className={untrusted ? "rounded-lg border border-amber-200 bg-amber-50 p-3" : "rounded-lg border border-slate-200 p-3"}
      {...(untrusted ? { "data-visual-constraint": "false" } : {})}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-slate-900">{fact.label}</span>
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${untrusted ? "bg-amber-100 text-amber-800" : "bg-blue-50 text-blue-700"}`}>
          {trustLabels[fact.trust]}
        </span>
      </div>
      <p className="mt-1">{fact.value}</p>
      {untrusted && <p className="mt-2 text-xs font-medium text-amber-800">仅供追溯，不作为视觉约束</p>}
      {(fact.sourceLine !== undefined || fact.evidence) && (
        <div className="mt-2 border-l-2 border-slate-200 pl-3 text-xs text-slate-500">
          {fact.sourceLine !== undefined && <p>来源行：{fact.sourceLine}</p>}
          {fact.evidence && <p>证据：{fact.evidence}</p>}
        </div>
      )}
    </li>
  );
}

export function CharacterVisualProfile({ biography, facts = [], visualProposal, visualIdentity, outfitsAndStates, promptSnapshot, advancedDiagnostics = false, className = "" }: CharacterVisualProfileProps) {
  return (
    <div className={`space-y-4 ${className}`.trim()}>
      <Section title="人物小传">{biography ? <p className="whitespace-pre-wrap">{biography}</p> : <EmptyValue />}</Section>
      <Section title="剧本明确事实">
        {facts.length ? <ul className="space-y-3">{facts.map((fact, index) => <FactItem key={fact.id ?? `${fact.label}-${index}`} fact={fact} />)}</ul> : <EmptyValue />}
      </Section>
      <Section title="视觉提案">{visualProposal ?? <EmptyValue />}</Section>
      <Section title="视觉身份设定">{visualIdentity ?? <EmptyValue />}</Section>
      <Section title="服装与状态">{outfitsAndStates ?? <EmptyValue />}</Section>
      {advancedDiagnostics && promptSnapshot && (
        <Section title="高级诊断">
          <div className="rounded-lg bg-slate-950 p-4 text-slate-100">
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">Prompt snapshot（只读）</p>
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-xs" aria-label="prompt snapshot">{promptSnapshot}</pre>
          </div>
        </Section>
      )}
    </div>
  );
}

export default CharacterVisualProfile;
