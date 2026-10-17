import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiCall } from '@/api/client';
import { Button } from '@/components/ui/button';
import { useTasks, useCancelTask } from '@/lib/queries/tasks';
import { useCanvasStore } from '@/stores/canvasStore';
import { CANVAS_NODE_TYPES } from '../domain/canvasNodes';
import { directorWorkspaceApi } from './directorWorkspaceApi';
import { useProductionAction } from './useProductionAction';
import { ProductionPreflight } from './ProductionPreflight';
import type { NarrativeGroupVideoSettings } from '@/lib/queries/narrative-groups';

interface MediaGroup { id: string; director_revision_id?: string; video_settings?: NarrativeGroupVideoSettings; stages: Record<string, { status: string; needs_regeneration?: boolean; video_asset?: string; error?: string }> }
const activeTaskStatuses = ['pending', 'queued', 'starting', 'running', 'submitting'];
const cancellableTaskStatuses = ['pending', 'queued', 'starting', 'running'];

function isExactBoundGroupTask(task: { metadata?: Record<string, unknown> }, revisionId: string, groupId: string): boolean {
  const metadata = task.metadata;
  if (metadata?.revision_id !== revisionId || !Array.isArray(metadata.group_ids)) return false;
  return metadata.group_ids.length === 1 && metadata.group_ids[0] === groupId;
}

export function DirectorProductionPanel({ project, episode, nodeId, revisionId, active, groups, groupId }: {
  project: string; episode: number; nodeId: string; revisionId: string; active: boolean; groups: Array<Record<string, unknown>>; groupId?: string;
}) {
  const [through, setThrough] = useState<'render' | 'video'>('video');
  const availableGroups = groups.map(group => String(group.id ?? group.group_id)).filter(Boolean);
  const [selection, setSelection] = useState<{ revisionId: string; groupIds: string[] }>(() => {
    if (groupId) return { revisionId, groupIds: [groupId] };
    const parent = useCanvasStore.getState().nodes.find(node => node.id === nodeId);
    const saved = parent?.data.productionGroupIds;
    return {
      revisionId,
      groupIds: parent?.data.directorRevisionId === revisionId && Array.isArray(saved)
        ? saved.filter((value): value is string => typeof value === 'string' && availableGroups.includes(value))
        : availableGroups,
    };
  });
  const updateNodeData = useCanvasStore(state => state.updateNodeData);
  const { busy, error, perform } = useProductionAction();
  const tasks = useTasks({ project, episode });
  const cancel = useCancelTask();
  const productionTasks = tasks.data?.data.filter(task => task.task_type === 'director_workspace_production') ?? [];
  const runningTasks = productionTasks.filter(task => activeTaskStatuses.includes(task.status));
  const running = runningTasks.length > 0;
  const nodeTasks = groupId
    ? productionTasks.filter(task => isExactBoundGroupTask(task, revisionId, groupId))
    : productionTasks;
  const hasOtherRunningTask = Boolean(groupId && runningTasks.some(task => !isExactBoundGroupTask(task, revisionId, groupId)));
  const media = useQuery({ queryKey: ['director-production-media', project, episode, revisionId, productionTasks.map(task => task.status).join(',')],
    queryFn: () => apiCall<MediaGroup[]>(`projects/${encodeURIComponent(project)}/episodes/${episode}/narrative-groups`), enabled: Boolean(project && revisionId), refetchInterval: running ? 5000 : false });
  const scopedGroups = groupId ? groups.filter(group => String(group.id ?? group.group_id ?? '') === groupId) : groups;
  const available = scopedGroups.map(group => String(group.id ?? group.group_id));
  const chosen = groupId
    ? available
    : (selection.revisionId === revisionId ? selection.groupIds : available).filter(id => available.includes(id));
  const revisionMedia = (media.data ?? []).filter(group => group.director_revision_id === revisionId && available.includes(group.id));
  const saveSelection = (next: string[]) => {
    const normalized = [...new Set(next)].filter(id => availableGroups.includes(id));
    setSelection({ revisionId, groupIds: normalized });
    if (!groupId) updateNodeData(nodeId, { directorRevisionId: revisionId, productionGroupIds: normalized });
  };
  const submit = (retry: boolean) => void perform(`production:${revisionId}:${chosen.join(',')}:${through}:${retry}`, async mutation => {
    if (!groupId) {
      updateNodeData(nodeId, { directorRevisionId: revisionId, productionGroupIds: chosen });
    }
    await directorWorkspaceApi.produce(project, episode, revisionId, chosen, mutation, retry, through); await tasks.refetch(); await media.refetch();
  });
  return <section className="space-y-3 rounded-xl border p-4"><h3 className="font-medium">叙事组制作</h3>
    <p className="text-sm text-muted-foreground">采用导演方案后，按保存的并发设置制作所选叙事组。导出成片在剪辑节点单独执行。</p>
    {(error || media.error || cancel.error) && <p role="alert" className="text-sm text-destructive">{error || String(media.error || cancel.error)}</p>}
    {hasOtherRunningTask && <p className="text-sm text-muted-foreground" role="status">本集其他制作任务进行中，请回导演工作空间查看并管理。</p>}
    {groupId
      ? <p className="text-sm text-muted-foreground">当前节点绑定组：{String(scopedGroups[0]?.title ?? scopedGroups[0]?.objective ?? groupId)}</p>
      : <div className="flex flex-wrap gap-3">{scopedGroups.map(group => { const id = String(group.id ?? group.group_id); return <label className="text-sm" key={id}><input type="checkbox" checked={chosen.includes(id)} onChange={event => saveSelection(event.target.checked ? [...chosen, id] : chosen.filter(value => value !== id))} /> {String(group.title ?? group.objective ?? id)}</label>; })}</div>}
    <fieldset className="flex flex-wrap gap-4 text-sm" disabled={busy || running}>
      <legend className="mb-1 font-medium">制作终点</legend>
      <label className="flex items-center gap-2"><input type="radio" name={`director-through-${nodeId}`} value="render" checked={through === 'render'} onChange={() => setThrough('render')} />仅制作分镜图</label>
      <label className="flex items-center gap-2"><input type="radio" name={`director-through-${nodeId}`} value="video" checked={through === 'video'} onChange={() => setThrough('video')} />制作分镜图和视频</label>
    </fieldset>
    {active && chosen.length > 0 && <ProductionPreflight project={project} episode={episode} revisionId={revisionId} groupIds={chosen} settings={Object.fromEntries(revisionMedia.map(group => [group.id, group.video_settings]))} through={through} />}
    <div className="flex flex-wrap gap-2">{!groupId && <Button variant="outline" onClick={() => saveSelection(available)}>选择全部叙事组</Button>}<Button disabled={!active || !chosen.length || busy || running} onClick={() => submit(false)}>{through === 'render' ? '开始制作所选组分镜图' : '开始所选组制作'}</Button><Button variant="outline" disabled={!active || !chosen.length || busy || running} onClick={() => submit(true)}>恢复所选组并重试失败阶段</Button></div>
    {nodeTasks.map(task => <div key={task.scope} className="rounded border p-3 text-sm"><p role="status">{task.status} · {task.current_task ?? ''}</p>{task.error && <p role="alert">{task.error}</p>}{cancellableTaskStatuses.includes(task.status) && <Button variant="outline" disabled={cancel.isPending} onClick={() => cancel.mutate({ type: task.task_type, project, episode, scope: task.scope })}>停止制作协调</Button>}</div>)}
    {revisionMedia.map(group => { const video = group.stages.video; return <div key={group.id} className="rounded border p-3 text-sm"><p>{group.id} · 分镜 {group.stages.render?.status ?? '未制作'} · 视频 {video?.status ?? '未制作'}{video?.needs_regeneration ? ' · 来源已更新' : ''}</p>{(video?.error || group.stages.render?.error) && <p role="alert">{video?.error || group.stages.render?.error}</p>}{active && available.includes(group.id) && video?.status === 'completed' && video.video_asset && !video.needs_regeneration && <><video controls preload="none" className="mt-2 max-h-64" src={video.video_asset} /><Button className="mt-2" variant="outline" onClick={() => {
      const store = useCanvasStore.getState(); if (store.nodes.some(node => node.data.videoUrl === video.video_asset)) return;
      const source = store.nodes.find(node => node.id === nodeId);
      const target = store.addNode(CANVAS_NODE_TYPES.video, { x: (source?.position.x ?? 0) + 520, y: (source?.position.y ?? 0) + available.indexOf(group.id) * 280 }, { videoUrl: video.video_asset, displayName: `E${episode} · ${group.id}`, referenceOnly: true, episodeNumber: episode, groupId: group.id, directorRevisionId: revisionId });
      if (target) store.addEdgeWithData(nodeId, target, { contextLink: true });
    }}>将组视频加入画布剪辑素材</Button></>}</div>; })}
  </section>;
}
