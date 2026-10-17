import { useEffect, useState, type ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { resolveMediaUrl } from '@/lib/media-url';
import { DirectorGroupStructureEditor } from '../DirectorGroupStructureEditor';
import { DirectorShotEditor } from '../DirectorShotEditor';
import { DirectorShotViews, type DirectorPreviewGroup } from '../DirectorShotViews';
import type { SceneBlocking } from '../SceneBlockingEditor';
import type { DirectorStage, SemanticCandidate } from '../directorWorkspaceApi';
import { ProductionWorkspaceLayout } from './ProductionWorkspaceLayout';

type RecordValue = Record<string, unknown>;
type View = 'board' | 'table' | 'animatic';

const viewLabels: Record<View, string> = { board: '图板', table: '镜头表', animatic: '时间预演' };
const stageNames: Record<string, string> = {
  handoff: '剧本交接', semantics: '来源拆解', director_plan: '导演规划',
  director_version: '导演画面版', asset_breakdown: '资产拆解', group_plan: '叙事组方案',
};
const stageStatusNames: Record<string, string> = {
  pending: '等待中', waiting: '排队中', running: '进行中', completed: '已完成',
  failed: '失败', cancelled: '已取消', skipped: '已跳过',
};

function groupIdOf(group: RecordValue): string {
  return String(group.group_id ?? group.id ?? '');
}

function groupTitleOf(group: RecordValue, groupId: string): string {
  return String(group.title ?? group.label ?? group.name ?? groupId);
}

function shotsOf(group: RecordValue | undefined): RecordValue[] {
  return Array.isArray(group?.shots) ? group.shots as RecordValue[] : [];
}

function shotIdOf(shot: RecordValue, index: number): string {
  return String(shot.id ?? index + 1);
}

function shotLabel(shot: RecordValue, index: number): string {
  return `${index + 1}. ${String(shot.shot_size ?? shot.framing ?? '镜头')} · ${shotIdOf(shot, index)}`;
}

export interface DirectorWorkspaceProps {
  project: string;
  episode: number;
  title: string;
  revisionId: string;
  revisionStatus?: string;
  revisions: Array<Record<string, unknown>>;
  groups: Record<string, unknown>[];
  stages: Record<string, DirectorStage>;
  sceneFor: (group: Record<string, unknown>) => string | undefined;
  sceneBlockings?: Record<string, { current?: { blocking?: SceneBlocking } }>;
  previewSemantics?: SemanticCandidate;
  previewMedia?: DirectorPreviewGroup[];
  mediaError?: unknown;
  actionError?: string;
  workspaceError?: unknown;
  busy: boolean;
  sourceReview?: ReactNode;
  scheduleReview?: ReactNode;
  onBack: () => void;
  onSelectRevision: (revisionId: string) => void;
  onAdopt: () => void;
  onRefresh: () => void;
  onOpenStoryboard: (group: Record<string, unknown>) => void;
  onSavedRevision: (revision: Record<string, unknown> | null) => void;
}

export function DirectorWorkspace({
  project, episode, title, revisionId, revisionStatus, revisions, groups, stages, sceneFor,
  sceneBlockings, previewSemantics, previewMedia = [], mediaError, actionError, workspaceError,
  busy, sourceReview, scheduleReview, onBack, onSelectRevision, onAdopt, onRefresh,
  onOpenStoryboard, onSavedRevision,
}: DirectorWorkspaceProps) {
  const [view, setView] = useState<View>('board');
  const [selectedGroupId, setSelectedGroupId] = useState(() => groups.length ? groupIdOf(groups[0]) : '');
  const [selectedShotId, setSelectedShotId] = useState('');
  const [openReview, setOpenReview] = useState<'source' | 'schedule' | null>(null);

  useEffect(() => {
    if (!groups.some(group => groupIdOf(group) === selectedGroupId)) {
      setSelectedGroupId(groups.length ? groupIdOf(groups[0]) : '');
      setSelectedShotId('');
    }
  }, [groups, selectedGroupId]);

  const selectedGroup = groups.find(group => groupIdOf(group) === selectedGroupId);
  const shots = shotsOf(selectedGroup);
  const selectedShot = shots.find((shot, index) => shotIdOf(shot, index) === selectedShotId) ?? shots[0];
  const selectedShotIndex = selectedShot ? shots.indexOf(selectedShot) : -1;
  const selectedGroupIdValue = selectedGroup ? groupIdOf(selectedGroup) : '';
  const selectedGroupTitle = selectedGroup ? groupTitleOf(selectedGroup, selectedGroupIdValue) : '';
  const selectedMedia = revisionStatus === 'active'
    ? previewMedia.find(media => media.id === selectedGroupIdValue && media.director_revision_id === revisionId)
    : undefined;
  const boardUrl = selectedMedia?.stages.render?.grid_asset && !selectedMedia.stages.render.needs_regeneration
    ? resolveMediaUrl(selectedMedia.stages.render.grid_asset) : null;
  const statusLabel = revisionStatus === 'active' ? '已采用版本' : revisionStatus ? '候选版本' : '尚无导演版本';

  const navigation = <div className="space-y-5">
    <section aria-label="场景与叙事组">
      <h3 className="mb-2 px-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">场景与叙事组</h3>
      {groups.length ? <ul className="space-y-1">{groups.map(group => {
        const groupId = groupIdOf(group);
        const label = groupTitleOf(group, groupId);
        const scene = sceneFor(group);
        return <li key={groupId}>
          <Button type="button" className="h-auto w-full justify-start whitespace-normal px-3 py-2 text-left" variant={selectedGroupId === groupId ? 'secondary' : 'ghost'} aria-pressed={selectedGroupId === groupId}
            onClick={() => { setSelectedGroupId(groupId); setSelectedShotId(shotsOf(group).length ? shotIdOf(shotsOf(group)[0], 0) : ''); }}>
            <span className="min-w-0"><span className="block truncate">{label}</span>{scene && <span className="block truncate text-xs text-muted-foreground">{scene}</span>}</span>
          </Button>
        </li>;
      })}</ul> : <p className="px-2 text-sm text-muted-foreground">导演准备完成后会显示叙事组。</p>}
    </section>
  </div>;

  const inspector = <div className="space-y-4">
    {selectedGroup ? <>
      <section className="space-y-2">
        <div><h3 className="font-semibold">{selectedGroupTitle}</h3><p className="text-sm text-muted-foreground">{String(selectedGroup.objective ?? selectedGroup.narrative_purpose ?? selectedGroup.purpose ?? '未填写叙事目的')}</p>
          <p className="text-xs text-muted-foreground">{shots.length} 个镜头 · {sceneFor(selectedGroup) ?? '未关联场景'} · {statusLabel} {revisionId}</p></div>
        <Button type="button" className="w-full" variant="outline" disabled={!revisionId || !selectedGroupIdValue} onClick={() => onOpenStoryboard(selectedGroup)}>在画布中打开组分镜</Button>
      </section>
      <section className="space-y-2" aria-label="选择镜头">
        <h4 className="text-sm font-medium">组内镜头</h4>
        {shots.length ? <div className="space-y-1">{shots.map((shot, index) => <Button key={shotIdOf(shot, index)} type="button" className="h-auto w-full justify-start whitespace-normal px-2 py-2 text-left" variant={selectedShot && shotIdOf(selectedShot, selectedShotIndex) === shotIdOf(shot, index) ? 'secondary' : 'ghost'} aria-pressed={Boolean(selectedShot && shotIdOf(selectedShot, selectedShotIndex) === shotIdOf(shot, index))} onClick={() => setSelectedShotId(shotIdOf(shot, index))}>{shotLabel(shot, index)}</Button>)}</div>
          : <p className="text-sm text-muted-foreground">此叙事组当前没有镜头。</p>}
      </section>
      {selectedShot && <section className="space-y-2 rounded-lg border p-3">
        <h4 className="font-medium">{shotLabel(selectedShot, selectedShotIndex)}</h4>
        <p className="text-sm">{String(selectedShot.subject ?? selectedShot.description ?? selectedShot.visual_description ?? selectedShot.action ?? '暂无画面摘要')}</p>
        <p className="text-xs text-muted-foreground">{Number(selectedShot.duration_seconds ?? 0) || '未设'} 秒 · 场景 {String(selectedShot.scene_id ?? sceneFor(selectedGroup) ?? '未标注')}</p>
        <DirectorShotEditor key={`${revisionId}:${selectedGroupIdValue}:${shotIdOf(selectedShot, selectedShotIndex)}`} project={project} episode={episode} revisionId={revisionId} shot={selectedShot} group={selectedGroup} shotIndex={selectedShotIndex} media={selectedMedia} onSaved={onSavedRevision} />
      </section>}
      <DirectorGroupStructureEditor project={project} episode={episode} revisionId={revisionId} groups={groups} group={selectedGroup} onSaved={onSavedRevision} />
    </> : <p className="text-sm text-muted-foreground">导演准备完成后显示关联叙事组、镜头详情与编辑入口。</p>}

    <section className="space-y-2 border-t pt-3" aria-label="来源与调度审阅">
      <h3 className="text-sm font-medium">来源与调度审阅</h3>
      <div className="flex flex-wrap gap-2">
        {sourceReview && <Button type="button" size="sm" variant={openReview === 'source' ? 'secondary' : 'outline'} aria-expanded={openReview === 'source'} onClick={() => setOpenReview(current => current === 'source' ? null : 'source')}>来源审阅</Button>}
        {scheduleReview && <Button type="button" size="sm" variant={openReview === 'schedule' ? 'secondary' : 'outline'} aria-expanded={openReview === 'schedule'} onClick={() => setOpenReview(current => current === 'schedule' ? null : 'schedule')}>调度审阅</Button>}
      </div>
      {openReview === 'source' && <div className="rounded-lg border p-3">{sourceReview}</div>}
      {openReview === 'schedule' && <div className="rounded-lg border p-3">{scheduleReview}</div>}
    </section>

    <details className="border-t pt-3">
      <summary className="cursor-pointer text-sm font-medium">版本与任务状态</summary>
      <div className="mt-3 space-y-3">
        {revisions.length > 0 && <label className="grid gap-1 text-sm">导演版本<select className="min-w-0 rounded border bg-background p-2" value={revisionId} onChange={event => onSelectRevision(event.target.value)}>{revisions.map((item, index) => <option key={String(item.revision_id)} value={String(item.revision_id)}>方案 {index + 1} · {String(item.status ?? '')}{item.source_stale ? ' · 来源已更新' : ''}</option>)}</select></label>}
        {Object.entries(stages).length > 0 ? <div className="space-y-2">{Object.entries(stages).map(([key, stage]) => <div key={key} className="rounded border p-2"><strong className="text-sm">{stageNames[key] ?? key}</strong><p className="text-sm text-muted-foreground">{stageStatusNames[stage.status] ?? stage.status}</p>{stage.error && <p role="alert" className="text-sm text-destructive">{stage.error}</p>}</div>)}</div> : <p className="text-sm text-muted-foreground">当前没有阶段任务。</p>}
      </div>
    </details>
  </div>;

  const actions = <div className="flex flex-wrap items-center justify-between gap-3">
    <p className="text-sm text-muted-foreground">导演工作区负责编辑、采用方案和审阅结果；媒体任务从对应画布节点启动。</p>
    <div className="flex gap-2"><Button type="button" variant="outline" onClick={onRefresh}>刷新任务与版本</Button><Button type="button" disabled={busy || !revisionId} onClick={onAdopt}>采用导演方案</Button></div>
  </div>;

  return <ProductionWorkspaceLayout title={title} status={`${statusLabel}${revisionId ? ` · ${revisionId}` : ''}`} onBack={onBack} navigation={navigation} inspector={inspector} actions={actions}>
    <div className="space-y-4">
      {Boolean(actionError || workspaceError) && <p role="alert" className="text-sm text-destructive">{actionError || String(workspaceError)}</p>}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b pb-3">
        <div><h3 className="font-semibold">{selectedGroupTitle || '导演组分镜审阅'}</h3><p className="text-sm text-muted-foreground">在左侧切换叙事组，选择视图并在右侧检查详情。</p></div>
        <div className="flex flex-wrap gap-2" role="group" aria-label="分镜查看方式">{(Object.keys(viewLabels) as View[]).map(mode => <Button key={mode} type="button" variant={view === mode ? 'default' : 'outline'} aria-pressed={view === mode} onClick={() => setView(mode)}>{viewLabels[mode]}</Button>)}</div>
      </div>
      {Boolean(mediaError) && view === 'animatic' && <p role="alert" className="text-sm text-destructive">无法读取已有预演素材：{String(mediaError)}</p>}
      {view === 'animatic' && <p className="text-sm text-muted-foreground">时间预演使用当前采用版本已有媒体；缺失素材以文字占位显示，不会提交生成任务。</p>}
      {selectedGroup ? view === 'board' ? <section aria-label="组内镜头图板" className="space-y-3">
        {boardUrl ? <img className="max-h-[420px] w-full rounded-lg border object-contain" src={boardUrl} alt={`${selectedGroupTitle} 已有分镜图`} />
          : <p className="rounded-lg border border-dashed p-5 text-sm text-muted-foreground">当前导演版本没有可用分镜图，镜头内容以文字卡片显示。</p>}
        <div className="grid gap-3 sm:grid-cols-2">{shots.map((shot, index) => <button key={shotIdOf(shot, index)} type="button" className={`rounded-lg border p-3 text-left transition-colors hover:bg-muted/40 ${selectedShot && shotIdOf(selectedShot, selectedShotIndex) === shotIdOf(shot, index) ? 'border-primary bg-muted/40' : ''}`} aria-pressed={Boolean(selectedShot && shotIdOf(selectedShot, selectedShotIndex) === shotIdOf(shot, index))} onClick={() => setSelectedShotId(shotIdOf(shot, index))}>
          <span className="text-sm font-medium">{shotLabel(shot, index)}</span><span className="mt-1 block text-sm">{String(shot.subject ?? shot.description ?? shot.visual_description ?? shot.action ?? '暂无画面摘要')}</span><span className="mt-1 block text-xs text-muted-foreground">{Number(shot.duration_seconds ?? 0) || '未设'} 秒</span>
        </button>)}</div>
        {!shots.length && <p className="rounded-lg border border-dashed p-5 text-sm text-muted-foreground">此叙事组当前没有镜头。</p>}
      </section> : <DirectorShotViews groups={[{ ...selectedGroup, scene_id: sceneFor(selectedGroup) }]} view={view} semantics={previewSemantics} sceneBlockings={sceneBlockings} mediaGroups={revisionStatus === 'active' ? previewMedia.filter(media => media.director_revision_id === revisionId) : []} />
        : <div className="rounded-lg border border-dashed p-8 text-center"><h3 className="font-medium">尚无可审阅叙事组</h3><p className="mt-2 text-sm text-muted-foreground">导演准备完成后会在这里显示组分镜；来源审阅和任务状态可从右侧按需查看。</p></div>}
    </div>
  </ProductionWorkspaceLayout>;
}
