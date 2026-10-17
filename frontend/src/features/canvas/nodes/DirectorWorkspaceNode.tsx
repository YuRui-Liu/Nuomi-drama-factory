import { memo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { NodeProps } from '@xyflow/react';
import { apiCall } from '@/api/client';
import { Button } from '@/components/ui/button';
import { readUrl } from '@/lib/url-params';
import type { ProductionContentNodeData } from '../domain/canvasNodes';
import { CANVAS_NODE_TYPES } from '../domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import { directorWorkspaceApi } from '../production/directorWorkspaceApi';
import { SceneBlockingEditor, type SceneBlocking } from '../production/SceneBlockingEditor';
import { useProductionAction } from '../production/useProductionAction';
import { SemanticReview } from '../production/SemanticReview';
import { ProductionNodeShell } from '../production/ProductionNodeShell';
import { DirectorWorkspace } from '../production/workspace/DirectorWorkspace';
import type { DirectorPreviewGroup } from '../production/DirectorShotViews';

type SceneBlockingWorkspaceEntry = {
  current?: { revision_id: string; blocking: SceneBlocking };
  group_references?: Array<{ group_id: string; director_revision_id?: string | null; revision_id: string }>;
};
type DirectorCanvasState = ReturnType<typeof useCanvasStore.getState>;

export const DirectorWorkspaceNode = memo(function DirectorWorkspaceNode({ id, data }: NodeProps) {
  const d = data as ProductionContentNodeData;
  const project = readUrl().project ?? '';
  const episode = d.episodeNumber ?? 1;
  const { busy, error, perform } = useProductionAction();
  const [scene, setScene] = useState('');
  const [groupIds, setGroupIds] = useState<string[]>([]);
  const client = useQueryClient();
  const update = useCanvasStore(state => state.updateNodeData);
  const revisionsKey = ['director-plan-revisions', project, episode];
  const revisions = useQuery({ queryKey: revisionsKey, queryFn: () => apiCall<Array<Record<string, unknown>>>(`projects/${encodeURIComponent(project)}/episodes/${episode}/director-plans`), enabled: Boolean(project) });
  const setCandidateRevision = (value: Record<string, unknown> | null) => {
    if (!value) return;
    client.setQueryData<Array<Record<string, unknown>>>(revisionsKey, previous => [...(previous ?? []).filter(item => item.revision_id !== value.revision_id), value]);
    update(id, { directorRevisionId: String(value.revision_id), productionGroupIds: undefined });
  };
  const workspace = useQuery({ queryKey: ['director-workspace', project, episode], queryFn: () => directorWorkspaceApi.get(project, episode), enabled: Boolean(project),
    refetchInterval: query => Object.values(query.state.data?.stages ?? {}).some(stage => ['pending', 'waiting', 'running'].includes(stage.status)) ? 4000 : false });
  const revision = revisions.data?.find(item => item.revision_id === d.directorRevisionId) ?? workspace.data?.revision;
  const revisionId = typeof revision?.revision_id === 'string' ? revision.revision_id : '';
  const activeRevision = revisions.data?.find(item => item.status === 'active')
    ?? (workspace.data?.revision?.status === 'active' ? workspace.data.revision : undefined);
  const activeRevisionId = typeof activeRevision?.revision_id === 'string' ? activeRevision.revision_id : '';
  const activeGroups = Array.isArray(activeRevision?.groups) ? activeRevision.groups as Array<Record<string, unknown>> : [];
  const previewMedia = useQuery({
    queryKey: ['director-preview-media', project, episode, revisionId],
    queryFn: () => apiCall<DirectorPreviewGroup[]>(`projects/${encodeURIComponent(project)}/episodes/${episode}/narrative-groups`),
    enabled: Boolean(project && revisionId && revision?.status === 'active'),
  });
  const groups = Array.isArray(revision?.groups) ? revision.groups as Array<Record<string, unknown>> : workspace.data?.groups ?? [];
  const sceneFor = (group: Record<string, unknown>): string | undefined => {
    const scene = (group.creative_context as Record<string, unknown> | undefined)?.scene_id
      ?? workspace.data?.groups?.find(item => item.id === group.id)?.scene_id;
    return typeof scene === 'string' ? scene : undefined;
  };
  const sceneData = workspace.data?.scene_blocking?.[scene] as SceneBlockingWorkspaceEntry | undefined;
  const sceneReferences = sceneData?.group_references ?? [];
  const pendingAdoptionGroupIds = new Set(sceneReferences
    .filter(reference => revision?.status !== 'active' && reference.director_revision_id === revisionId)
    .map(reference => reference.group_id));
  const staleSceneReferences = sceneReferences.filter(reference => {
    if (pendingAdoptionGroupIds.has(reference.group_id)) return false;
    if (!reference.director_revision_id || reference.director_revision_id !== activeRevisionId) return true;
    const activeGroup = activeGroups.find(group => String(group.id ?? group.group_id ?? '') === reference.group_id);
    return !activeGroup || sceneFor(activeGroup) !== scene;
  });
  const selectedShots = groups.filter(group => groupIds.includes(String(group.id ?? group.group_id))).flatMap(group => Array.isArray(group.shots) ? group.shots as Array<Record<string, unknown>> : []);
  const bindings = (field: 'subjects' | 'lights', key: string) => [...new Set(selectedShots.flatMap(shot => {
    const cinema = shot.cinematography as Record<string, unknown> | undefined;
    return Array.isArray(cinema?.[field]) ? (cinema[field] as Array<Record<string, unknown>>).map(item => String(item[key])) : [];
  }))];
  const propIds = [...new Set(selectedShots.flatMap(shot => Array.isArray(shot.asset_requirements)
    ? (shot.asset_requirements as Array<Record<string, unknown>>)
      .filter(requirement => requirement.kind === 'prop' && typeof requirement.entity_key === 'string')
      .map(requirement => requirement.entity_key as string)
    : []))];
  const scenes = [...new Set(groups.map(sceneFor).filter((value): value is string => typeof value === 'string'))];
  const previewSemantics = workspace.data?.semantics?.candidates.find(candidate => candidate.revision_id === revision?.semantic_revision_id)
    ?? workspace.data?.semantics?.candidates.find(candidate => candidate.revision_id === workspace.data?.semantics?.active_revision_id);
  const sceneBlockings = workspace.data?.scene_blocking as Record<string, { current?: { blocking?: SceneBlocking } }> | undefined;

  const openStoryboard = (group: Record<string, unknown>) => {
    const stableGroupId = group.group_id ?? group.id;
    if (!project || !revisionId || stableGroupId === undefined || stableGroupId === null || !String(stableGroupId)) return;
    const state = useCanvasStore.getState() as DirectorCanvasState & {
      nodes: Array<{ id: string; type: string; data: Record<string, unknown> }>;
      edges?: Array<{ source: string; target: string; data?: Record<string, unknown> }>;
      addNode: (type: string, position: { x: number; y: number }, nodeData: Record<string, unknown>) => string | null;
      addEdgeWithData?: (source: string, target: string, edgeData: Record<string, unknown>) => string | null;
      findNodePosition: (nodeId: string, width: number, height: number) => { x: number; y: number };
      setSelectedNode: (nodeId: string) => void;
      requestFocusNode: (nodeId: string) => void;
    };
    const groupId = String(stableGroupId);
    const existing = state.nodes.find(node => node.type === CANVAS_NODE_TYPES.narrativeGroupStoryboard
      && node.data.episodeNumber === episode
      && String(node.data.groupId ?? '') === groupId
      && String(node.data.directorRevisionId ?? '') === revisionId
      && (!node.data.projectId || String(node.data.projectId) === project));
    const groupTitle = String(group.title ?? group.label ?? groupId);
    const nodeId = existing?.id ?? state.addNode(CANVAS_NODE_TYPES.narrativeGroupStoryboard,
      state.findNodePosition(id, 560, 520), {
        displayName: `E${String(episode).padStart(2, '0')} · ${groupTitle} · 组分镜`,
        projectId: project,
        episodeNumber: episode,
        groupId,
        directorRevisionId: revisionId,
      });
    if (!nodeId) return;
    if (existing && !existing.data.projectId) update(nodeId, { projectId: project });
    if (!state.edges?.some(edge => edge.source === id && edge.target === nodeId && edge.data?.contextLink === true)) {
      state.addEdgeWithData?.(id, nodeId, { contextLink: true });
    }
    state.setSelectedNode(nodeId);
    state.requestFocusNode(nodeId);
  };

  const scheduleReview = <section className="space-y-3" aria-label="共享场景调度">
    <h3 className="font-medium">共享场景调度</h3>
    <label className="grid gap-1 text-sm">场景<select className="rounded-md border bg-background p-2" value={scene} onChange={event => { setScene(event.target.value); setGroupIds(groups.filter(group => sceneFor(group) === event.target.value).map(group => String(group.id ?? group.group_id))); }}><option value="">选择导演方案中的场景</option>{[...new Set([...scenes, ...Object.keys(workspace.data?.scene_blocking ?? {})])].map(value => <option key={value} value={value}>{value}</option>)}</select></label>
    <div className="flex flex-wrap gap-2">{Object.keys(workspace.data?.scene_blocking ?? {}).map(key => <Button key={key} type="button" variant="outline" onClick={() => { setScene(key); setGroupIds(groups.filter(group => sceneFor(group) === key).map(group => String(group.id ?? group.group_id))); }}>{key}</Button>)}</div>
    {staleSceneReferences.length > 0 && <p role="status" className="text-sm text-amber-700">本场景有 {staleSceneReferences.length} 条旧版本或旧组引用，编译会阻止使用；请解除引用或按当前导演版本重新保存。</p>}
    {pendingAdoptionGroupIds.size > 0 && <p role="status" className="text-sm text-amber-700">当前调度引用固定在候选导演版本 {revisionId}；采用该版本后才可编译，保存不会自动采用。</p>}
    {sceneData?.current && sceneReferences.length > 0 && <Button type="button" variant="outline" disabled={busy} onClick={() => void perform(`unpin-blocking:${scene}:${sceneData.current!.revision_id}`, async () => { await directorWorkspaceApi.unpinBlocking(project, episode, scene, sceneData.current!.revision_id); await workspace.refetch(); })}>解除本场景全部组引用</Button>}
    {scene && <fieldset className="space-y-2"><legend className="text-sm font-medium">关联叙事组</legend>{groups.filter(group => sceneFor(group) === scene).map(group => {
      const groupId = String(group.id ?? group.group_id);
      return <label key={groupId} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={groupIds.includes(groupId)} disabled={busy}
        onChange={event => setGroupIds(current => event.target.checked ? [...new Set([...current, groupId])] : current.filter(value => value !== groupId))} />{String(group.title ?? group.name ?? groupId)}</label>;
    })}</fieldset>}
    {scene && <p className="text-xs text-muted-foreground">保存时，本场景关联组会精确替换为上方勾选范围；空选会清除此场景的有效组引用。旧场景可解除引用并保留调度历史。</p>}
    {scene && <SceneBlockingEditor key={`${scene}:${sceneData?.current?.revision_id ?? 'new'}`} initial={sceneData?.current?.blocking} subjects={bindings('subjects', 'subject_id')} lightIds={bindings('lights', 'light_id')} propIds={propIds} busy={busy} onSave={blocking => void perform(`blocking:${scene}:${revisionId}:${JSON.stringify(blocking)}:${groupIds.join(',')}`, async () => { await directorWorkspaceApi.saveBlocking(project, episode, scene, sceneData?.current?.revision_id ?? null, blocking as unknown as Record<string, unknown>, groupIds, revisionId || undefined); await workspace.refetch(); })} />}
    {sceneData?.current && <Button type="button" disabled={busy || revision?.status !== 'active' || sceneReferences.length === 0 || staleSceneReferences.length > 0} onClick={() => void perform(`compile:${scene}:${sceneData.current!.revision_id}`, async () => { setCandidateRevision(await directorWorkspaceApi.compileBlocking(project, episode, revisionId)); })}>将已关联调度编译为导演候选</Button>}
  </section>;

  const adopt = () => void perform(`adopt:${revisionId}`, async () => {
    await directorWorkspaceApi.adopt(project, episode, revisionId);
    await Promise.all([
      client.invalidateQueries({ queryKey: ['director-plan', project, episode] }),
      client.invalidateQueries({ queryKey: ['director-preview-media', project, episode] }),
      client.invalidateQueries({ queryKey: ['director-production-media', project, episode] }),
    ]);
    await Promise.all([revisions.refetch(), workspace.refetch()]);
  });

  return <ProductionNodeShell title={`E${String(episode).padStart(2, '0')} · 导演工作空间`} summary="编辑并审阅导演方案；媒体任务从对应画布节点启动" workspace>
    {({ requestClose }) => <DirectorWorkspace
      project={project}
      episode={episode}
      title={`E${String(episode).padStart(2, '0')} · 导演工作空间`}
      revisionId={revisionId}
      revisionStatus={typeof revision?.status === 'string' ? revision.status : undefined}
      revisions={revisions.data ?? []}
      groups={groups}
      stages={workspace.data?.stages ?? {}}
      sceneFor={sceneFor}
      sceneBlockings={sceneBlockings}
      previewSemantics={previewSemantics}
      previewMedia={previewMedia.data ?? []}
      mediaError={previewMedia.error}
      actionError={error}
      workspaceError={workspace.error}
      busy={busy}
      sourceReview={<SemanticReview project={project} episode={episode} semantics={workspace.data?.semantics} refresh={() => workspace.refetch()} />}
      scheduleReview={scheduleReview}
      onBack={requestClose}
      onSelectRevision={value => update(id, { directorRevisionId: value, productionGroupIds: undefined })}
      onAdopt={adopt}
      onRefresh={() => { void workspace.refetch(); void revisions.refetch(); }}
      onOpenStoryboard={openStoryboard}
      onSavedRevision={setCandidateRevision}
    />}
  </ProductionNodeShell>;
});
