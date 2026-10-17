import { Button } from '@/components/ui/button';
import type { DirectorHandoffPreview, DirectorHandoffScope } from './directorWorkspaceApi';

const stageNames: Record<string, string> = {
  episode_source: '剧本来源', screenplay_semantics: '剧本语义', director_plan: '导演方案',
  narrative_group_media: '叙事组素材', timeline_exports: '时间线导出',
};

function listLines(lines: string[] | undefined, marker: string) {
  if (!lines?.length) return <span className="text-muted-foreground">（无）</span>;
  return lines.slice(0, 5).map((line, index) => <span key={`${index}:${line}`} className="block">{marker} {line}</span>);
}

export function ScreenplayHandoffPreview({ preview, mode, sceneIds, busy, canConfirm, onMode, onToggleScene, onRefresh, onClose, onConfirm }: {
  preview: DirectorHandoffPreview;
  mode: DirectorHandoffScope['mode'];
  sceneIds: string[];
  busy: boolean;
  canConfirm: boolean;
  onMode: (value: DirectorHandoffScope['mode']) => void;
  onToggleScene: (id: string, checked: boolean) => void;
  onRefresh: () => void;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const scopeLabel = mode === 'none' ? '只更新剧本来源，保留现有导演场景调度'
    : mode === 'all' ? '同步全部场次，相关导演阶段需重新复核' : `仅同步已选 ${sceneIds.length} 场`;
  const canConfirmScope = preview.scope_options[mode] && (mode !== 'selected' || sceneIds.length > 0);
  return <section className="space-y-4 rounded-lg border border-primary/40 bg-muted/30 p-4" aria-label="剧本交接差异与影响预览">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h3 className="font-medium">交给导演前 · 版本差异与同步范围</h3>
        <p className="text-xs text-muted-foreground">候选修订 {preview.revision_id} · 项目来源基线 {preview.expected_source_project_revision}
          {preview.current_source ? ` · 当前来源修订 ${preview.current_source.revision}` : ' · 尚无剧本来源'}</p></div>
      <div className="flex gap-2"><Button size="sm" variant="outline" disabled={busy} onClick={onRefresh}>重新读取差异</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={onClose}>关闭</Button></div>
    </div>
    <div className="rounded border bg-background p-3 text-sm">
      <strong>{preview.operation === 'added' ? '新增剧本来源' : preview.operation === 'replaced' ? '替换剧本来源' : '剧本来源内容未变化'}</strong>
      <p className="mt-1 text-xs text-muted-foreground">候选内容指纹 {preview.candidate_script.hash.slice(0, 16)}
        {preview.current_source?.hash ? ` · 当前指纹 ${preview.current_source.hash.slice(0, 16)}` : ''}</p>
    </div>
    {preview.diff.needs_reparse && <p role="alert" className="text-sm text-amber-700">当前剧本无法稳定解析全部场次；按场选择不可用。可选择整体同步或仅更新剧本来源。</p>}
    <details open className="rounded border bg-background p-3">
      <summary className="cursor-pointer text-sm font-medium">正文差异（{preview.diff.text.length} 处）</summary>
      {preview.diff.text.length ? <ol className="mt-2 max-h-72 space-y-3 overflow-auto text-xs">{preview.diff.text.slice(0, 20).map((change, index) => <li key={`${change.operation}:${index}`} className="rounded bg-muted/40 p-2">
        <span className="font-medium">{change.operation === 'insert' ? '新增' : change.operation === 'delete' ? '删除' : '替换'} · 原行 {change.old_lines[0]}–{change.old_lines[1]} / 新行 {change.new_lines[0]}–{change.new_lines[1]}</span>
        <pre className="mt-1 whitespace-pre-wrap">{listLines(change.before, '−')}{listLines(change.after, '+')}</pre>
      </li>)}</ol> : <p className="mt-2 text-xs text-muted-foreground">正文文本没有变化。</p>}
      {preview.diff.text.length > 20 && <p className="mt-2 text-xs text-muted-foreground">其余 {preview.diff.text.length - 20} 处差异已折叠。</p>}
    </details>
    <div className="grid gap-3 md:grid-cols-2">
      <section className="rounded border bg-background p-3"><h4 className="text-sm font-medium">场次身份与变化</h4>
        {preview.diff.scenes.length ? <ul className="mt-2 space-y-2 text-xs">{preview.diff.scenes.map((change, index) => <li key={`${change.operation}:${index}`}>
          <strong>{change.operation === 'insert' ? '新增场次' : change.operation === 'delete' ? '移除场次' : change.operation === 'replace' ? '场次内容变化' : '场次重排'}</strong>
          <span className="block text-muted-foreground">来源：{change.old_scene_ids.join(', ') || '无'} → 候选：{change.new_scene_ids.join(', ') || '无'}</span>
        </li>)}</ul> : <p className="mt-2 text-xs text-muted-foreground">场次正文没有变化。</p>}
        {preview.diff.reused_scenes.length > 0 && <p className="mt-2 text-xs text-muted-foreground">延续原身份：{preview.diff.reused_scenes.map(item => item.new_scene_id).join('、')}</p>}
        {preview.candidate_script.scene_identities.filter(item => item.parent_ids.length).length > 0 && <details className="mt-3 text-xs">
          <summary className="cursor-pointer">拆合继承关系</summary><ul className="mt-1 space-y-1">{preview.candidate_script.scene_identities.filter(item => item.parent_ids.length).map(item => <li key={item.id}>{item.id} ← {item.parent_ids.join(', ')}</li>)}</ul>
        </details>}
      </section>
      <section className="rounded border bg-background p-3"><h4 className="text-sm font-medium">对白差异</h4>
        {preview.diff.dialogue.length ? <ul className="mt-2 max-h-52 space-y-2 overflow-auto text-xs">{preview.diff.dialogue.slice(0, 12).map((change, index) => <li key={`${change.operation}:${index}`}>
          <strong>{change.operation === 'insert' ? '新增' : change.operation === 'delete' ? '删除' : '修改'}</strong>
          {change.before.length > 0 && <pre className="whitespace-pre-wrap text-muted-foreground">{listLines(change.before, '−')}</pre>}
          {change.after.length > 0 && <pre className="whitespace-pre-wrap">{listLines(change.after, '+')}</pre>}
        </li>)}</ul> : <p className="mt-2 text-xs text-muted-foreground">对白没有变化。</p>}
      </section>
    </div>
    <section className="space-y-2 rounded border bg-background p-3">
      <h4 className="text-sm font-medium">选择本次同步范围</h4>
      <div className="grid gap-2 md:grid-cols-3">
        {([
          ['none', '保留导演场景数据', '更新剧本来源，不更新现有导演场景调度。'],
          ['all', '同步全部场次', '刷新所有候选场景，相关下游阶段需复核。'],
          ['selected', '只同步指定场次', '勾选需要刷新的候选场景。'],
        ] as const).map(([value, label, description]) => <label key={value} className={`rounded border p-2 text-xs ${mode === value ? 'border-primary bg-accent' : ''}`}>
          <span className="flex items-center gap-2"><input type="radio" name="screenplay-handoff-scope" value={value} checked={mode === value}
            disabled={!preview.scope_options[value]} onChange={() => onMode(value)} /> <strong>{label}</strong></span>
          <span className="mt-1 block text-muted-foreground">{value === 'selected' && !preview.scope_options.selected ? preview.scope_options.selected_reason ?? description : description}</span>
        </label>)}
      </div>
      {mode === 'selected' && <div className="grid gap-2 sm:grid-cols-2">{preview.available_scenes.map(scene => <label key={scene.id} className="flex items-start gap-2 rounded border p-2 text-xs">
        <input type="checkbox" checked={sceneIds.includes(scene.id)} onChange={event => onToggleScene(scene.id, event.target.checked)} />
        <span><strong>{scene.heading}</strong><span className="block text-muted-foreground">{scene.location || '地点未标注'} · {scene.id}</span></span>
      </label>)}</div>}
      <p className="text-xs">当前选择：{scopeLabel}</p>
      <p className="text-xs text-muted-foreground">可能受影响的阶段：{preview.affected_stages.map(stage => stageNames[stage] ?? stage).join('、') || '无下游阶段变化'}。此操作仅准备剧本交接，不会启动图像、视频或音频制作。</p>
    </section>
    {!canConfirm && <p role="status" className="text-xs text-amber-700">当前工作区有未保存修改或预览版本已过期；请先保存并重新读取差异。</p>}
    <div className="flex justify-end"><Button disabled={busy || !canConfirm || !canConfirmScope} onClick={onConfirm}>确认此范围并交给导演</Button></div>
  </section>;
}
