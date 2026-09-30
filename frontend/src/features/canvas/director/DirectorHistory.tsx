import type { DirectorAttempt } from '../domain/canvasNodes';
import { useTranslation } from 'react-i18next';
import { directorStageLabel } from './directorStatus';

export function DirectorHistory({ attempts, activeId, onRetry, onRefresh }: { attempts: DirectorAttempt[]; activeId: string | null; onRetry: (id: string) => void; onRefresh: () => void }) {
  const { t } = useTranslation();
  const tr = (key: string, defaultValue: string) => t(`node.videoDirector.editor.${key}`, { defaultValue });
  return <section className="mt-4 border-t border-white/10 pt-4" aria-label="生成历史">
    <div className="mb-2 flex justify-between"><h3 className="text-sm font-medium">{tr('history', '生成历史')}</h3><button type="button" onClick={onRefresh} className="text-xs text-cyan-300">{tr('refreshHistory', '刷新历史')}</button></div>
    {!attempts.length && <p className="text-xs text-text-muted">{tr('emptyHistory', '暂无生成记录')}</p>}
    <div className="space-y-2">{attempts.map((attempt) => <details key={attempt.id} className="rounded-lg border border-white/10 p-2 text-xs">
      <summary className="cursor-pointer">{attempt.id === activeId ? `${tr('current', '当前')} · ` : ''}{directorStageLabel(attempt.stage, t)} · {tr('revision', '修订')} {attempt.revision} · {attempt.createdAt ?? ''}</summary>
      <div className="mt-2 space-y-1 text-text-muted">
        <p>{tr('originalPrompt', '原始提示词')}：{attempt.snapshot.segments.map((segment) => segment.prompt).join(' / ')}</p>
        {attempt.optimized && <><p>{tr('optimizedPrompt', '优化提示词')}：{attempt.optimized.segments.map((segment) => segment.prompt).join(' / ')}</p>
          <p>{tr('route', '路线')}：{attempt.optimized.route} · {tr('profile', '配置')}：{attempt.optimized.profileId} v{attempt.optimized.profileVersion}</p></>}
        {Object.entries(attempt.frozenTechniques ?? {}).map(([segmentId, frozen]) => <div key={segmentId} className="rounded border border-white/10 p-2">
          <p>{tr('segment', '分段')} {segmentId} · {frozen.card.title} v{frozen.card.version} · {frozen.card.content_hash}</p>
          <p>{frozen.card.summary}</p>
          {frozen.card.sources?.map((source) => <a key={source.url} href={source.url} target="_blank" rel="noreferrer" className="mr-2 text-cyan-300">{source.credit}</a>)}
          <p>{tr('optimizedPrompt', '优化提示词')}：{attempt.optimized?.segments.find((segment) => segment.segmentId === segmentId)?.prompt ?? '—'}</p>
          <pre className="overflow-auto whitespace-pre-wrap">{JSON.stringify(frozen.projection, null, 2)}</pre>
        </div>)}
        <p>{tr('workflow', '工作流')}：{attempt.workflowId ?? '—'} · {attempt.workflowProfileId ?? '—'} v{attempt.workflowProfileVersion ?? '—'}</p>
        {attempt.actualParameters && <pre className="overflow-auto whitespace-pre-wrap">{JSON.stringify(attempt.actualParameters, null, 2)}</pre>}
        {attempt.resultUrl && <a href={attempt.resultUrl} target="_blank" rel="noreferrer" className="text-cyan-300">{tr('viewResult', '查看结果')}</a>}
        {attempt.error && <p role="alert" className="text-red-300">{attempt.error}</p>}
        {attempt.stage === 'failed' && <button type="button" className="text-cyan-300" onClick={() => onRetry(attempt.id)}>{attempt.failedStage === 'downloading' ? tr('retryDownload', '重试下载') : tr('retryGeneration', '重试生成')}</button>}
        {attempt.stage === 'submission_unknown' && <p className="text-amber-300">{tr('submissionUnknown', '提交状态未知，请人工核对任务，避免重复计费。')}</p>}
      </div>
    </details>)}</div>
  </section>;
}
