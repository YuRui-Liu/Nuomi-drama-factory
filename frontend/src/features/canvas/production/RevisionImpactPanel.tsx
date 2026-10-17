import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { scriptCreationApi } from '@/features/script-creation/api';
import type { ScriptDocument } from '@/features/script-creation/types';

export interface RevisionDependency {
  document: ScriptDocument;
  revisionId: string;
  downstream: string;
}

function changedLines(before: string, after: string): { lines: string[]; truncated: boolean } {
  const oldLines = before.split('\n');
  const newLines = after.split('\n');
  if (oldLines.length > 260 || newLines.length > 260) {
    let prefix = 0;
    while (prefix < oldLines.length && prefix < newLines.length && oldLines[prefix] === newLines[prefix]) prefix += 1;
    let suffix = 0;
    while (suffix < oldLines.length - prefix && suffix < newLines.length - prefix &&
      oldLines[oldLines.length - suffix - 1] === newLines[newLines.length - suffix - 1]) suffix += 1;
    return {
      lines: [
        ...oldLines.slice(prefix, Math.min(oldLines.length - suffix, prefix + 12)).map(line => `- ${line}`),
        ...newLines.slice(prefix, Math.min(newLines.length - suffix, prefix + 12)).map(line => `+ ${line}`),
      ],
      truncated: true,
    };
  }

  const rows = oldLines.length + 1;
  const columns = newLines.length + 1;
  const lcs = Array.from({ length: rows }, () => new Uint16Array(columns));
  for (let i = oldLines.length - 1; i >= 0; i -= 1) {
    for (let j = newLines.length - 1; j >= 0; j -= 1) {
      lcs[i][j] = oldLines[i] === newLines[j]
        ? lcs[i + 1][j + 1] + 1
        : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  const hunks: string[][] = [];
  let i = 0;
  let j = 0;
  let current: string[] = [];
  while (i < oldLines.length || j < newLines.length) {
    if (i < oldLines.length && j < newLines.length && oldLines[i] === newLines[j]) {
      if (current.length) hunks.push(current);
      current = [];
      i += 1;
      j += 1;
    } else if (i < oldLines.length && (j === newLines.length || lcs[i + 1][j] >= lcs[i][j + 1])) {
      current.push(`- ${oldLines[i]}`);
      i += 1;
    } else if (j < newLines.length) {
      current.push(`+ ${newLines[j]}`);
      j += 1;
    }
  }
  if (current.length) hunks.push(current);
  const shown = hunks.slice(0, 8).flatMap((hunk, index) => index ? ['…', ...hunk] : hunk);
  return { lines: shown.slice(0, 32), truncated: hunks.length > 8 || hunks.some(hunk => hunk.length > 32) };
}

function UpstreamRevision({ dependency, project }: { dependency: RevisionDependency; project: string }) {
  const query = useQuery({
    queryKey: ['upstream-revision-history', project, dependency.document.id],
    queryFn: () => scriptCreationApi.revisions(project, dependency.document.id),
    enabled: dependency.revisionId !== dependency.document.current_revision_id,
  });
  const frozen = query.data?.find(revision => revision.id === dependency.revisionId);
  const current = query.data?.find(revision => revision.id === dependency.document.current_revision_id)
    ?? dependency.document.revision;
  const diff = useMemo(() => frozen ? changedLines(frozen.markdown, current.markdown) : null,
    [current.markdown, frozen?.markdown]);

  if (dependency.revisionId === dependency.document.current_revision_id) return null;
  return <article className="space-y-2 rounded border border-amber-500/40 p-3">
    <div><h4 className="text-sm font-medium">{dependency.document.title} · 上游版本已变化</h4>
      <p className="text-xs text-muted-foreground">下游：{dependency.downstream}</p>
      <p className="text-xs text-muted-foreground">已采用 {dependency.revisionId} → 当前 {dependency.document.current_revision_id}</p></div>
    {query.error && <p role="alert" className="text-xs text-destructive">无法读取上游版本差异：{String(query.error)}</p>}
    {!query.error && !frozen && <p className="text-xs">正在读取已固定版本的正文差异…</p>}
    {frozen && diff && <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-muted/50 p-2 text-xs">{diff.lines.length ? diff.lines.join('\n') : '版本标识不同，但正文内容相同。'}{diff.truncated ? '\n… 其余差异已折叠' : ''}</pre>}
    <p className="text-xs text-amber-700">现有下游产物继续引用已确认版本；请人工复核后再重新规划或审稿，不会自动改写或启动媒体任务。</p>
  </article>;
}

export function RevisionImpactPanel({ project, dependencies, title = '上游版本与下游影响' }: {
  project: string; dependencies: RevisionDependency[]; title?: string;
}) {
  const documentsKey = dependencies.map(item => item.document.id).join('|');
  const latestDocuments = useQuery({
    queryKey: ['upstream-current-documents', project, documentsKey],
    queryFn: () => Promise.all(dependencies.map(item => scriptCreationApi.get(project, item.document.id))),
    enabled: dependencies.length > 0,
  });
  const currentById = new Map((latestDocuments.data ?? []).map(document => [document.id, document]));
  const currentDependencies = dependencies.map(item => ({
    ...item,
    document: currentById.get(item.document.id) ?? item.document,
  }));
  const stale = currentDependencies.filter(item => item.revisionId && item.revisionId !== item.document.current_revision_id);
  const identities = stale.map(item => `${item.document.id}:${item.revisionId}:${item.document.current_revision_id}`).join('|');
  if (!dependencies.length) return null;
  return <section className="space-y-3 rounded-lg border p-4" aria-label={title}>
    <div><h3 className="text-sm font-medium">{title}</h3>
      <p className="text-xs text-muted-foreground">下游保留其创建或确认时固定的上游版本。</p></div>
    {latestDocuments.error && <p role="alert" className="text-sm text-destructive">读取上游文档当前版本失败：{String(latestDocuments.error)}</p>}
    {latestDocuments.isLoading && <p className="text-sm text-muted-foreground">正在读取上游文档当前版本…</p>}
    {!latestDocuments.isLoading && stale.length === 0
      ? <p className="text-sm text-muted-foreground">目前可读取的上游文档都与已采用版本一致。</p>
      : stale.map((dependency, index) => <UpstreamRevision key={`${identities}:${index}`} dependency={dependency} project={project} />)}
  </section>;
}
