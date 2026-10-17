import { Button } from '@/components/ui/button';
import { directorWorkspaceApi, type DirectorWorkspace } from './directorWorkspaceApi';
import { useProductionAction } from './useProductionAction';

export function SemanticReview({ project, episode, semantics, refresh }: { project: string; episode: number; semantics?: DirectorWorkspace['semantics']; refresh: () => Promise<unknown> }) {
  const { busy, error, perform } = useProductionAction();
  if (!semantics?.candidates.length) return null;
  return <section className="space-y-3 rounded-xl border p-4"><h3 className="font-medium">剧本拆解审核</h3><p className="text-sm text-muted-foreground">核对场次、原文对白和戏剧节拍；采用拆解后，单独开始导演方案。</p>
    {error && <p role="alert" className="text-destructive">{error}</p>}
    {semantics.candidates.map((candidate, index) => <details key={candidate.revision_id} className="rounded border p-3"><summary>拆解 {index + 1} · {candidate.scenes.length} 场 · {candidate.beats.length} 个节拍{candidate.revision_id === semantics.active_revision_id ? ' · 已采用' : candidate.is_current_source ? '' : ' · 来源已更新'}</summary>
      {candidate.validation_report.issues.map((issue, i) => <p className="text-sm" key={`${issue.code}:${i}`}>{issue.severity === 'error' ? '需修复' : '提示'}：{issue.message}</p>)}
      {candidate.scenes.map(scene => <article key={scene.id} className="mt-3 space-y-2 border-t pt-3"><h4 className="font-medium">{scene.heading || scene.location}</h4><p className="text-xs">{scene.time_of_day} · {scene.characters.join('、')}</p><details><summary className="text-sm">原文与对白</summary>{scene.blocks.map(block => <p className="whitespace-pre-wrap text-sm" key={block.id}>{block.text}</p>)}</details>
        {candidate.beats.filter(beat => beat.scene_id === scene.id).map(beat => <div className="rounded bg-muted p-3 text-sm" key={beat.id}><p>目标：{beat.goal}；阻力：{beat.obstacle}</p><p>行动：{beat.action}；反应：{beat.reaction}</p><p>转折：{beat.turn}；结果：{beat.result}</p><p>情绪变化：{beat.emotional_shift}</p></div>)}
      </article>)}
      <Button className="mt-3" disabled={busy || !candidate.can_activate || candidate.revision_id === semantics.active_revision_id} onClick={() => void perform(`activate:${candidate.revision_id}`, async () => { await directorWorkspaceApi.activateSemantics(project, episode, candidate.revision_id); await refresh(); })}>采用此拆解</Button>
    </details>)}
    <Button disabled={busy || !semantics.active_revision_id} onClick={() => void perform(`continue:${semantics.active_revision_id}`, async () => { await directorWorkspaceApi.continue(project, episode, semantics.active_revision_id!); await refresh(); })}>依据已采用拆解生成导演方案</Button>
  </section>;
}
