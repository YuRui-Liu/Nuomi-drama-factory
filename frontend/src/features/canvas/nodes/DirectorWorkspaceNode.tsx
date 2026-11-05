import { memo, useEffect, useState } from 'react';
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
import { canEditEpisodeNode, useWorkspaceScope } from '@/features/freezone/WorkspaceScope';
import { DirectorWorkspace } from '../production/workspace/DirectorWorkspace';
import { DirectorProfessionalRuns } from '../production/workspace/DirectorProfessionalRuns';
import { DirectorProductionPanel } from '../production/DirectorProductionPanel';
import type { DirectorPreviewGroup } from '../production/DirectorShotViews';
import { getSceneDirectorStageManifest } from '@/api/viewerManifests';
import { uploadFreezoneImage } from '@/api/ops';
import { resolveMediaUrl } from '@/lib/media-url';
import type { ThreeDDirectorCaptureMeta } from '@/features/viewer-kit/three-d/ThreeDDirectorDialog';
import type { DirectorStageManifest } from '@/features/viewer-kit/three-d/directorManifest';

type SceneBlockingWorkspaceEntry = {
  current?: { revision_id: string; blocking: SceneBlocking };
  group_references?: Array<{ group_id: string; director_revision_id?: string | null; revision_id: string }>;
};
type DirectorCanvasState = ReturnType<typeof useCanvasStore.getState>;

function DirectorWorkspaceEditor({ id, data, project, episode }: {
  id: string;
  data: NodeProps['data'];
  project: string;
  episode: number;
}) {
  const d = data as ProductionContentNodeData;
  const { busy, error, perform } = useProductionAction();
  const scope = useWorkspaceScope();
  const [scene, setScene] = useState('');
  const [groupIds, setGroupIds] = useState<string[]>([]);
  const canvasNodes = useCanvasStore(state => state.nodes);
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
    refetchInterval: query => Object.values(query.state.data?.stages ?? {}).some(stage => stage.task_id && ['submitting', 'queued', 'pending', 'waiting', 'running'].includes(stage.status)) ? 4000 : false });
  const directorStage = workspace.data?.stages?.director_plan;
  const planning = Boolean(directorStage?.task_id && ['submitting', 'queued', 'pending', 'waiting', 'running'].includes(directorStage.status));
  const semanticId = workspace.data?.semantics?.active_revision_id;
  const sourceMatches = !d.sourceDocumentId || !workspace.data?.script || (
    workspace.data.script.document_id === d.sourceDocumentId
    && workspace.data.script.revision_id === d.sourceDocumentRevisionId
  );
  useEffect(() => {
    if (directorStage?.status === 'completed' || directorStage?.status === 'needs_review') {
      void client.invalidateQueries({ queryKey: ['director-plan-revisions', project, episode] });
    }
  }, [client, project, episode, directorStage?.status, directorStage?.revision_id]);
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
  useEffect(() => {
    if (scene || !scenes.length) return;
    const firstScene = scenes[0];
    setScene(firstScene);
    setGroupIds(groups.filter(group => sceneFor(group) === firstScene).map(group => String(group.id ?? group.group_id)));
  }, [groups, scene, scenes]);
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
      && !['render', 'retry_render'].includes(String(node.data.operation ?? ''))
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
    if (existing) update(nodeId, {
      projectId: project,
      episodeNumber: episode,
      groupId,
      directorRevisionId: revisionId,
      displayName: `E${String(episode).padStart(2, '0')} · ${groupTitle} · 组分镜`,
    });
    if (!state.edges?.some(edge => edge.source === id && edge.target === nodeId && edge.data?.contextLink === true)) {
      state.addEdgeWithData?.(id, nodeId, { contextLink: true });
    }
    state.setSelectedNode(nodeId);
    state.requestFocusNode(nodeId);
  };

  const openThreeDScene = async (sceneId: string): Promise<DirectorStageManifest | null> => {
    if (!project || !sceneId) return null;
    return getSceneDirectorStageManifest(project, sceneId);
  };

  const saveBlocking = async (sceneId: string, blocking: SceneBlocking) => {
    let saved = false;
    await perform(
      `blocking:${sceneId}:${revisionId}:${JSON.stringify(blocking)}:${groupIds.join(',')}`,
      async () => {
        await directorWorkspaceApi.saveBlocking(
          project, episode, sceneId, sceneData?.current?.revision_id ?? null,
          blocking as unknown as Record<string, unknown>, groupIds, revisionId || undefined,
        );
        await workspace.refetch();
        saved = true;
      },
    );
    if (!saved) throw new Error('场景调度没有保存成功，请检查错误后重试。');
  };

  const captureThreeD = async (sceneId: string, blob: Blob, meta: ThreeDDirectorCaptureMeta) => {
    const uploaded = await uploadFreezoneImage(
      project,
      blob,
      `director-scene-${episode}-${encodeURIComponent(sceneId)}-${meta.snapshot.savedAt}.png`,
      { timeoutMs: false },
    );
    return {
      filename: uploaded.filename,
      url: uploaded.url,
      captured_at: meta.snapshot.savedAt,
      scene_id: sceneId,
    };
  };

  const bindThreeDCapture = async (capture: NonNullable<SceneBlocking['stage_capture']>, selectedGroupIds: string[]) => {
    const targetNodes = selectedGroupIds.flatMap(groupId => {
      const node = canvasNodes.find(candidate => candidate.type === CANVAS_NODE_TYPES.narrativeGroupStoryboard
        && candidate.data.operation === 'render'
        && candidate.data.episodeNumber === episode
        && String(candidate.data.groupId ?? '') === groupId
        && String(candidate.data.directorRevisionId ?? '') === revisionId
        && (!candidate.data.projectId || String(candidate.data.projectId) === project));
      return node ? [{ groupId, node }] : [];
    });
    if (!targetNodes.length) throw new Error('当前场景还没有组分镜渲染节点。先创建整集制作图，再绑定这张 3D 截图。');

    const captureUrl = resolveMediaUrl(capture.url);
    if (!captureUrl) throw new Error('已保存的 3D 截图缺少可读取地址。');
    const response = await fetch(captureUrl);
    if (!response.ok) throw new Error(`无法读取已保存的 3D 截图（HTTP ${response.status}）。`);
    const blob = await response.blob();
    const bound: string[] = [];
    for (const { groupId, node } of targetNodes) {
      const currentIds = Array.isArray(node.data.selectedUploadIds)
        ? node.data.selectedUploadIds.filter((value): value is string => typeof value === 'string') : [];
      if (node.data.directorStageCaptureUrl === capture.url && currentIds.length > 0) {
        bound.push(groupId);
        continue;
      }
      const groupPath = `projects/${encodeURIComponent(project)}/episodes/${episode}/narrative-groups/${encodeURIComponent(groupId)}/render/references`;
      const preview = await apiCall<{ reference_revision: string; bindings: Array<Record<string, unknown>> }>(groupPath);
      const bindings = preview.bindings.filter(item => item.status === 'ready' && typeof item.binding_id === 'string');
      const existingBindings = Array.isArray(node.data.selectedBindingIds)
        ? node.data.selectedBindingIds.filter((value): value is string => typeof value === 'string') : [];
      const selectedBindingIds = existingBindings.length
        ? bindings.filter(item => existingBindings.includes(String(item.binding_id))).map(item => String(item.binding_id))
        : bindings.filter(item => item.selected_by_default === true).map(item => String(item.binding_id));
      const body = new FormData();
      const filename = capture.filename.split('/').pop() || `director-scene-${scene}.png`;
      body.append('file', new File([blob], filename, { type: blob.type || 'image/png' }));
      const upload = await apiCall<{ upload_id: string }>(`${groupPath}/upload`, { method: 'post', body, retry: 0 });
      update(node.id, {
        selectedBindingIds,
        selectedUploadIds: [...new Set([...currentIds, upload.upload_id])],
        referenceRevision: preview.reference_revision,
        directorStageCaptureUrl: capture.url,
        directorStageCaptureSceneId: capture.scene_id,
      });
      bound.push(groupId);
    }
    return bound;
  };

  const sceneCapture = sceneData?.current?.blocking?.stage_capture ?? null;
  const renderTargetGroupIds = canvasNodes.flatMap(node => node.type === CANVAS_NODE_TYPES.narrativeGroupStoryboard
    && node.data.operation === 'render'
    && node.data.episodeNumber === episode
    && String(node.data.directorRevisionId ?? '') === revisionId
    && (!node.data.projectId || String(node.data.projectId) === project)
    ? [String(node.data.groupId ?? '')] : []);
  const captureBoundGroupIds = sceneCapture
    ? groupIds.filter(groupId => canvasNodes.some(node => node.type === CANVAS_NODE_TYPES.narrativeGroupStoryboard
      && node.data.operation === 'render'
      && node.data.episodeNumber === episode
      && String(node.data.groupId ?? '') === groupId
      && String(node.data.directorRevisionId ?? '') === revisionId
      && node.data.directorStageCaptureUrl === sceneCapture.url
      && Array.isArray(node.data.selectedUploadIds)
      && node.data.selectedUploadIds.length > 0))
    : [];

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
    {scene && <SceneBlockingEditor key={`${scene}:${sceneData?.current?.revision_id ?? 'new'}`} initial={sceneData?.current?.blocking} subjects={bindings('subjects', 'subject_id')} lightIds={bindings('lights', 'light_id')} propIds={propIds} busy={busy} sceneId={scene} groupIds={groupIds} renderTargetGroupIds={renderTargetGroupIds} captureBoundGroupIds={captureBoundGroupIds} onOpenThreeD={() => openThreeDScene(scene)} onCaptureThreeD={(blob, meta) => captureThreeD(scene, blob, meta)} onBindThreeDCapture={bindThreeDCapture} onSave={blocking => saveBlocking(scene, blocking)} />}
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
      planningPanel={<><section aria-label="本集导演创作" className="rounded-lg border p-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h3 className="font-medium">本集导演创作</h3><p className="text-sm text-muted-foreground">以当前正式剧本和已采用的来源拆解创建导演候选，完成后审阅并采用。</p></div>
          <Button type="button" disabled={busy || planning || !semanticId || !sourceMatches}
            onClick={() => void perform(`director-plan:${semanticId}`, async () => {
              if (!semanticId || !sourceMatches) throw new Error('请先完成本集剧本交接并采用当前来源拆解。');
              const result = await directorWorkspaceApi.continue(project, episode, semanticId);
              client.setQueryData(['director-workspace', project, episode], result);
              await Promise.all([revisions.refetch(), workspace.refetch()]);
            })}>{planning ? '导演规划中…' : revisionId ? '继续本集导演规划' : '开始本集导演规划'}</Button>
        </div>
        {!sourceMatches && <p role="alert" className="mt-2 text-sm text-amber-700">此节点绑定的剧本已被更新，请从最新交接打开对应集画布。</p>}
        {!semanticId && <p className="mt-2 text-sm text-muted-foreground">请先在来源审阅中确认本集当前剧本的拆解结果。</p>}
      </section><DirectorProfessionalRuns runs={revision?.professional_runs} decision={revision?.professional_decision} /></>}
      sourceReview={<SemanticReview project={project} episode={episode} semantics={workspace.data?.semantics} refresh={() => workspace.refetch()} />}
      productionPanel={<DirectorProductionPanel
        project={project}
        episode={episode}
        revisionId={activeRevisionId}
        active={Boolean(activeRevisionId)}
        groups={activeGroups}
        canvasId={scope?.canvasId}
        workspaceNodeId={id}
        script={workspace.data?.script as {
          document_id?: string;
          revision_id?: string;
          source_sync?: { status?: string; message?: string; code?: string };
          artifact?: { status?: string; path?: string; sha256?: string; source_revision?: number; matches_episode_source?: boolean };
        } | undefined}
        semantics={previewSemantics}
        mediaGroups={previewMedia.data ?? []}
        onRefreshMedia={async () => {
          const refreshed = await previewMedia.refetch();
          return refreshed.data ?? [];
        }}
      />}
      scheduleReview={scheduleReview}
      onBack={requestClose}
      onSelectRevision={value => update(id, { directorRevisionId: value, productionGroupIds: undefined })}
      onAdopt={adopt}
      onRefresh={() => { void workspace.refetch(); void revisions.refetch(); }}
      onOpenStoryboard={openStoryboard}
      onSavedRevision={setCandidateRevision}
    />}
  </ProductionNodeShell>;
}

export const DirectorWorkspaceNode = memo(function DirectorWorkspaceNode(props: NodeProps) {
  const scope = useWorkspaceScope();
  const d = props.data as ProductionContentNodeData;
  const project = readUrl().project ?? '';
  const canEdit = scope?.projectId === project && canEditEpisodeNode(scope, d.episodeNumber);
  if (canEdit && scope) {
    return <DirectorWorkspaceEditor id={props.id} data={props.data}
      project={project} episode={scope.episodeNumber!} />;
  }

  const sourceEpisode = typeof d.episodeNumber === 'number' && Number.isInteger(d.episodeNumber) && d.episodeNumber > 0
    ? `E${String(d.episodeNumber).padStart(2, '0')}` : null;
  const currentEpisode = scope?.kind === 'episode' && scope.episodeNumber
    ? `E${String(scope.episodeNumber).padStart(2, '0')}` : scope?.kind === 'concept' ? '构思画布'
      : scope?.kind === 'free' ? '自由画布' : '未绑定分集画布';
  return <ProductionNodeShell title={sourceEpisode ? `${sourceEpisode} · 导演工作空间（只读）` : '导演工作空间待关联'}
    summary="该节点只能在其绑定的分集画布中编辑。" status={`来源：${sourceEpisode ?? '待关联'}；当前：${currentEpisode}`}
    actionLabel="查看关联提示">
    <p className="text-sm text-muted-foreground">请切换到节点对应的分集画布后再打开导演工作空间。此处保留来源信息，不会读取或修改导演方案。</p>
  </ProductionNodeShell>;
});
