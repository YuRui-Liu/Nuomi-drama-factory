import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { scriptCreationApi } from '@/features/script-creation/api';
import type { ConsistencyEvidence, ConsistencyRun, ScriptDocument } from '@/features/script-creation/types';
import { useProductionAction } from './useProductionAction';
import { productionActionErrorFallback } from './productionActionError';

export type ScreenplayLocateTarget = { document_id: string; revision_id: string; offset: number };

type LocateResult = { offset: number; reason: null } | { offset: null; reason: string };

function locateQuote(markdown: string, quote: string): LocateResult {
  const offset = markdown.indexOf(quote);
  if (!quote || offset < 0) return { offset: null, reason: '引文在正文中不存在，无法定位' };
  if (markdown.indexOf(quote, offset + 1) >= 0) return { offset: null, reason: '引文在正文中不唯一，无法定位' };
  return { offset, reason: null };
}

function contextMatches(run: ConsistencyRun, current: Record<string, string>) {
  const revisions = run.context_revisions ?? {};
  const keys = Object.keys(revisions);
  return keys.length === Object.keys(current).length && keys.every(id => revisions[id] === current[id]);
}

function findEvidenceTarget(evidence: ConsistencyEvidence, documents: ScriptDocument[], locatableDocumentIds: Set<string>): LocateResult & { document?: ScriptDocument } {
  if (!locatableDocumentIds.has(evidence.document_id)) return { offset: null, reason: '该文档当前未打开，无法定位' };
  const sourceDocument = documents.find(item => item.id === evidence.document_id);
  if (!sourceDocument) return { offset: null, reason: '来源文档不在当前审阅范围，无法定位' };
  if (sourceDocument.current_revision_id !== evidence.revision_id || sourceDocument.revision.id !== evidence.revision_id) {
    return { offset: null, reason: '依据版本已变化，无法定位', document: sourceDocument };
  }
  const location = locateQuote(sourceDocument.revision.markdown, evidence.quote);
  return { ...location, document: sourceDocument };
}

const statusLabels: Record<ConsistencyRun['status'], string> = {
  pending: '等待检查', running: '正在检查', completed: '已完成当前版本检查', failed: '检查失败', needs_rebase: '需要重新检查',
};

export function ScreenplayReview({ project, document, references, saved, refresh, rewritePending, onLocate, onCheckReadyChange, locatableDocumentIds }: {
  project: string; document: ScriptDocument; references: ScriptDocument[]; saved: boolean;
  refresh: () => Promise<unknown>; rewritePending: boolean;
  onLocate?: (target: ScreenplayLocateTarget) => void;
  onCheckReadyChange?: (ready: boolean) => void;
  locatableDocumentIds?: string[];
}) {
  const { busy, error, perform } = useProductionAction();
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const reportedReadiness = useRef<{ key: string; ready: boolean } | null>(null);
  const runs = useQuery({ queryKey: ['screenplay-consistency', project, document.id],
    queryFn: () => scriptCreationApi.listConsistencyRuns(project, document.id),
    refetchInterval: query => query.state.data?.some(run => ['pending', 'running'].includes(run.status)) ? 3000 : false });
  const proposals = useQuery({ queryKey: ['screenplay-proposals', project, document.id],
    queryFn: () => scriptCreationApi.listProposals(project, document.id), refetchInterval: rewritePending ? 3000 : false });
  const context = Object.fromEntries([document, ...references.filter(item => item.id !== document.id)].map(item => [item.id, item.current_revision_id]));
  const actualRuns = (runs.data ?? []).filter(run => run.mode === 'actual' && run.episode_document_id === document.id);
  const currentRuns = actualRuns.filter(run => contextMatches(run, context));
  const latest = currentRuns[0];
  const completedRuns = currentRuns.filter(run => run.status === 'completed');
  const unresolvedFactIssues = completedRuns.flatMap(run => run.issues).filter(issue =>
    issue.category === 'fact' && !issue.proposal_id && !issue.intentional_reason?.trim());
  const checkReady = completedRuns.length > 0 && unresolvedFactIssues.length === 0;
  const readinessKey = JSON.stringify([document.id, context]);
  const issues = [...new Map(completedRuns.flatMap(run => run.issues).map(issue => [issue.id, issue])).values()];
  const allDocuments = [document, ...references.filter(item => item.id !== document.id)];
  const locatableIds = new Set(locatableDocumentIds ?? [document.id]);

  useEffect(() => {
    if (!onCheckReadyChange || (reportedReadiness.current?.key === readinessKey && reportedReadiness.current.ready === checkReady)) return;
    reportedReadiness.current = { key: readinessKey, ready: checkReady };
    onCheckReadyChange(checkReady);
  }, [checkReady, onCheckReadyChange, readinessKey]);

  const contextLabel = latest
    ? statusLabels[latest.status] ?? latest.status
    : actualRuns.length ? '检查已过期' : '尚未检查';
  return <section aria-label="剧本版本审查" className="space-y-3 rounded-lg border p-3">
    <div className="flex flex-wrap items-center gap-3"><h3 className="font-medium">事实与连续性</h3>
      <Button variant="outline" disabled={busy || !saved || runs.data?.some(run => ['pending', 'running'].includes(run.status))} onClick={() => void perform(`consistency:${JSON.stringify(context)}`, async mutation => {
        await scriptCreationApi.startConsistencyRun(project, { episode_document_id: document.id, context_revisions: context, proposal_id: null, client_mutation_id: mutation });
        await runs.refetch();
      })}>检查当前剧本</Button>
      <span className="text-sm text-muted-foreground" role="status">{contextLabel}</span>
    </div>
    {checkReady && <p className="text-xs text-muted-foreground">检查依据涵盖当前剧本和关联文档版本。</p>}
    {unresolvedFactIssues.length > 0 && <p className="text-xs text-amber-700 dark:text-amber-300">有待处理的事实问题</p>}
    {!latest && actualRuns.length > 0 && <p className="text-xs text-amber-700 dark:text-amber-300">剧本或关联文档已更新，请重新检查当前版本。</p>}
    {(error || runs.error || proposals.error) && <p role="alert" className="text-destructive">{error || productionActionErrorFallback(runs.error || proposals.error)}</p>}
    {latest?.error && <p role="alert">{latest.error}</p>}
    {issues.map(issue => {
      const sourceLocation = issue.source ? findEvidenceTarget(issue.source, allDocuments, locatableIds) : null;
      const targetLocation = issue.target ? findEvidenceTarget(issue.target, allDocuments, locatableIds) : null;
      const canLocateSource = Boolean(saved && onLocate && sourceLocation?.document && sourceLocation.offset !== null);
      const canLocateTarget = Boolean(saved && onLocate && targetLocation?.document && targetLocation.offset !== null);
      return <article key={issue.id} className="space-y-2 rounded border p-3 text-sm">
      <div className="flex flex-wrap items-baseline gap-x-1"><span className="font-medium">{issue.category === 'fact' ? '事实问题' : '创作建议'}</span><span>·</span><span>{issue.explanation}</span>
        {issue.stale && <span className="text-amber-700 dark:text-amber-300">（依据已变化，需重新检查）</span>}
      </div>
      {issue.source && <div className="space-y-1">
        <blockquote className="border-l-2 pl-3 whitespace-pre-wrap">{issue.source.quote}</blockquote>
        <div className="flex flex-wrap items-center gap-2"><Button variant="outline" size="sm" disabled={!canLocateSource} onClick={() => {
          if (saved && onLocate && sourceLocation?.document && sourceLocation.offset !== null) onLocate({ document_id: issue.source!.document_id, revision_id: issue.source!.revision_id, offset: sourceLocation.offset });
        }}>定位依据</Button>
          {!canLocateSource && <span className="text-xs text-muted-foreground">{!saved ? '请先保存当前编辑后定位' : !onLocate ? '文档联动暂不可用' : sourceLocation?.reason}</span>}
        </div>
      </div>}
      {issue.target && <div className="space-y-1">
        <blockquote className="border-l-2 pl-3 whitespace-pre-wrap">{issue.target.quote}</blockquote>
        <div className="flex flex-wrap items-center gap-2"><Button variant="outline" size="sm" disabled={!canLocateTarget} onClick={() => {
          if (saved && onLocate && targetLocation?.document && targetLocation.offset !== null) onLocate({ document_id: issue.target!.document_id, revision_id: issue.target!.revision_id, offset: targetLocation.offset });
        }}>定位剧本</Button>
          {!canLocateTarget && <span className="text-xs text-muted-foreground">{!saved ? '请先保存当前编辑后定位' : !onLocate ? '文档联动暂不可用' : targetLocation?.reason}</span>}
        </div>
      </div>}
      {issue.suggested_action && <p className="text-xs text-muted-foreground">建议：{issue.suggested_action}</p>}
      {issue.intentional_reason ? <p className="text-xs text-muted-foreground">已说明创作意图：{issue.intentional_reason}</p> : <div className="flex flex-wrap gap-2">
        <input aria-label="保留此设定的理由" className="min-w-0 flex-1 rounded border bg-background p-2" placeholder="如属有意设定，说明理由" value={reasons[issue.id] ?? ''} onChange={event => setReasons(previous => ({ ...previous, [issue.id]: event.target.value }))} />
        <Button variant="outline" disabled={busy || issue.stale || !reasons[issue.id]?.trim()} onClick={() => void perform(`intentional:${issue.id}:${reasons[issue.id]}`, async () => { await scriptCreationApi.markIntentional(project, issue.id, reasons[issue.id]); await runs.refetch(); })}>记录理由</Button>
      </div>}
    </article>;
    })}
    {proposals.data?.filter(proposal => proposal.document_id === document.id && proposal.status === 'pending').map(proposal => <article key={proposal.id} className="space-y-3 rounded border p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2"><h3 className="font-medium">主笔修改候选</h3><p className="text-xs text-muted-foreground">{proposal.reason}</p></div>
      <details className="rounded border px-3 py-2">
        <summary className="cursor-pointer text-sm">查看修改对比</summary>
        <div className="mt-3 grid gap-3 md:grid-cols-2"><div><p className="text-xs text-muted-foreground">原文</p><pre className="whitespace-pre-wrap text-sm">{proposal.before}</pre></div><div><p className="text-xs text-muted-foreground">候选</p><pre className="whitespace-pre-wrap text-sm">{proposal.after}</pre></div></div>
      </details>
      <div className="flex gap-2"><Button disabled={busy || !saved || proposal.base_revision_id !== document.current_revision_id} onClick={() => void perform(`proposal:${proposal.id}`, async mutation => {
        await scriptCreationApi.acceptProposals(project, [proposal.id], document.current_revision_id, mutation); await refresh(); await proposals.refetch();
      })}>采用此修改</Button><Button variant="outline" disabled={busy} onClick={() => void perform(`discard:${proposal.id}`, async () => { await scriptCreationApi.discardProposal(project, proposal.id); await proposals.refetch(); })}>放弃候选</Button></div>
      {proposal.base_revision_id !== document.current_revision_id && <p className="text-sm text-amber-600">正文版本已变化，请基于当前版本重新委托。</p>}
    </article>)}
  </section>;
}
