import { useQueries } from '@tanstack/react-query';
import { apiCall } from '@/api/client';
import { useTaskConcurrency } from '@/lib/queries/task-concurrency';
import type { NarrativeGroupVideoSettings, PlannedNarrativeGroupReferencePreview } from '@/lib/queries/narrative-groups';

export function ProductionPreflight({ project, episode, revisionId, groupIds, settings, through = 'video' }: {
  project: string; episode: number; revisionId: string; groupIds: string[];
  settings: Record<string, NarrativeGroupVideoSettings | undefined>;
  through?: 'render' | 'video';
}) {
  const concurrency = useTaskConcurrency(Boolean(project));
  const references = useQueries({ queries: groupIds.map(group => ({
    queryKey: ['production-preflight-references', project, episode, revisionId, group],
    queryFn: () => apiCall<PlannedNarrativeGroupReferencePreview>(`projects/${encodeURIComponent(project)}/episodes/${episode}/narrative-groups/${encodeURIComponent(group)}/render/references`),
    staleTime: 0,
  })) });
  return <section aria-label="制作前检查" className="space-y-2 rounded border bg-muted/30 p-3 text-sm">
    <p>本次范围：E{String(episode).padStart(2, '0')} · {groupIds.length} 个叙事组 · {through === 'render' ? '整组分镜图' : '整组分镜图 → 组视频'}</p>
    {concurrency.data ? <p>保存并发：图像 {concurrency.data.lanes.default.configured}{through === 'video' ? `，视频 ${concurrency.data.lanes.video.configured}` : ''}{concurrency.data.restart_required ? '；配置待重启，实际执行仍受运行中限额约束' : ''}</p> : <p>{concurrency.error ? '并发配置读取失败，提交时服务端将重新校验' : '正在读取保存的并发配置…'}</p>}
    <p>已完成且来源未变的产物复用；恢复操作只重试失败阶段。费用尚无法准确估算，新增生成按实际供应商计费。</p>
    {groupIds.map((group, index) => { const query = references[index]; const config = settings[group]; return <div key={group} className="border-t pt-2">
      {through === 'video' && <p>{group} · 视频工作流：{config?.workflow_id || '项目默认（提交时解析）'}</p>}
      {through === 'video' && config && Object.entries(config.overrides).length > 0 && <p>组参数：{Object.entries(config.overrides).map(([key, value]) => `${key}=${value}`).join('，')}</p>}
      {query.isPending ? <p>正在核对引用…</p> : query.error ? <p role="alert">引用检查失败：{String(query.error)}</p> : query.data && <>
        <p>引用版本：{query.data.reference_revision}</p>
        {query.data.bindings.filter(binding => binding.required || binding.selected_by_default).map(binding => <p key={binding.binding_id}>{binding.display_label}：{binding.status === 'ready' ? '可用' : binding.issue_message || binding.status}{binding.required ? '（必需）' : ''}{binding.repair_action ? ` · ${binding.repair_action}` : ''}</p>)}
        {query.data.warnings?.map((warning, i) => <p key={i}>{warning}</p>)}
      </>}
    </div>; })}
  </section>;
}
