import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { creativeWorkflowApi } from './creativeWorkflowApi';
import { useProductionAction } from './useProductionAction';
import { productionActionErrorFallback } from './productionActionError';
import type { ScreenplayLocateTarget } from './ScreenplayReview';

function uniqueQuoteOffset(markdown: string | undefined, markdownRevisionId: string | undefined, revisionId: string, quote: string) {
  if (!markdownRevisionId || markdownRevisionId !== revisionId) return { offset: null, reason: '正文版本与审稿结果不一致，无法定位' };
  if (!markdown) return { offset: null, reason: '当前正文暂不可用，无法定位' };
  const offset = markdown.indexOf(quote);
  if (!quote || offset < 0) return { offset: null, reason: '引文在正文中不存在，无法定位' };
  if (markdown.indexOf(quote, offset + 1) >= 0) return { offset: null, reason: '引文在正文中不唯一，无法定位' };
  return { offset, reason: null };
}

export function ManualScreenplayReview({ project, documentId, revisionId, planRunId, saved, markdown, markdownRevisionId, onLocate }: {
  project: string; documentId: string; revisionId: string; planRunId: string; saved: boolean;
  markdown?: string; markdownRevisionId?: string; onLocate?: (target: ScreenplayLocateTarget) => void;
}) {
  const { busy, error, perform } = useProductionAction();
  const review = useQuery({ queryKey: ['screenplay-revision-review', project, documentId, revisionId],
    queryFn: () => creativeWorkflowApi.screenplayReview(project, documentId, revisionId), enabled: Boolean(project && documentId && revisionId),
    refetchInterval: query => ['pending', 'running', 'submission_unknown'].includes(query.state.data?.status ?? '') ? 3000 : false });
  const active = ['pending', 'running', 'submission_unknown'].includes(review.data?.status ?? '');
  const labels: Record<string, string> = { pending: '等待审稿', running: '正在审稿', submission_unknown: '提交结果待确认，正在查询原任务', passed: '此版审稿通过', needs_revision: '此版需要修改', needs_rebase: '规划或正文已变化', failed: '审稿失败' };
  return <section className="space-y-3 rounded border p-3"><h3 className="text-sm font-medium">当前正文独立审稿</h3><p className="text-xs text-muted-foreground">手工编辑或采用选段修改后，可针对当前已保存正文重新审稿；不会自动改写正文。</p>
    {(error || review.error || review.data?.error) && <p role="alert" className="text-destructive">{error || review.data?.error || productionActionErrorFallback(review.error)}</p>}
    <Button variant="outline" disabled={busy || active || !saved || !planRunId || review.data?.status === 'passed'} onClick={() => void perform(`review:${documentId}:${revisionId}:${planRunId}`, async mutation => { await creativeWorkflowApi.reviewScreenplay(project, documentId, revisionId, planRunId, mutation); await review.refetch(); })}>提交当前版独立审稿</Button>
    {review.data && <p role="status">{labels[review.data.status] ?? review.data.status}</p>}
    {review.data?.result && <>
      {review.data.result.issues?.length ? <div className="space-y-2" aria-label="审稿问题">
        {review.data.result.issues.map(issue => {
          const location = uniqueQuoteOffset(markdown, markdownRevisionId, revisionId, issue.quote);
          return <article key={issue.id} className="space-y-2 rounded border p-3 text-sm">
            <p className="font-medium">{issue.severity === 'blocker' ? '必须修改' : '建议修改'} · {issue.reason}</p>
            <blockquote className="border-l-2 pl-3 whitespace-pre-wrap">{issue.quote}</blockquote>
            <p className="text-xs text-muted-foreground">修订目标：{issue.repair_goal}</p>
            <div className="flex flex-wrap items-center gap-2"><Button variant="outline" size="sm" disabled={!saved || !onLocate || location.offset === null} onClick={() => {
              if (saved && onLocate && location.offset !== null) onLocate({ document_id: documentId, revision_id: revisionId, offset: location.offset });
            }}>定位原文</Button>
              {(!saved || !onLocate || location.offset === null) && <span className="text-xs text-muted-foreground">{!saved ? '请先保存当前编辑后定位' : !onLocate ? '文档联动暂不可用' : location.reason}</span>}
            </div>
          </article>;
        })}
      </div> : <p className="text-sm text-muted-foreground">此版本没有待修改问题。</p>}
      <details className="rounded border px-3 py-2">
        <summary className="cursor-pointer text-sm">完整审稿摘要与优点</summary>
        <div className="mt-2 space-y-2 text-sm">
          {review.data.result.summary && <p>{review.data.result.summary}</p>}
          {review.data.result.coverage?.length ? <p className="text-xs text-muted-foreground">审查覆盖：{review.data.result.coverage.join('、')}</p> : null}
          {review.data.result.strengths?.map((item, index) => <p key={index}>有效表达：{item.quote} · {item.reason}</p>)}
        </div>
      </details>
    </>}
  </section>;
}
