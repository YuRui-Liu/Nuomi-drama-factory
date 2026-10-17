import { memo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { NodeProps } from '@xyflow/react';
import { apiCall } from '@/api/client';
import { Button } from '@/components/ui/button';
import { readUrl } from '@/lib/url-params';
import { resolveMediaUrl } from '@/lib/media-url';
import { useCanvasStore } from '@/stores/canvasStore';
import { connectNarrativeGroupGrid } from '../production/grid/connectNarrativeGroupGrid';
import { validateGridBinding, type GroupGridBinding } from '../production/grid/gridBinding';
import type { ProductionContentNodeData } from '../domain/canvasNodes';
import { DirectorShotEditor } from '../production/DirectorShotEditor';
import { DirectorGroupStructureEditor } from '../production/DirectorGroupStructureEditor';
import { DirectorShotViews, type DirectorPreviewGroup } from '../production/DirectorShotViews';
import { directorWorkspaceApi } from '../production/directorWorkspaceApi';
import { ProductionNodeShell } from '../production/ProductionNodeShell';

type RecordValue = Record<string, unknown>;
type StoryboardView = 'board' | 'table' | 'animatic';

const viewLabels: Record<StoryboardView, string> = { board: '图板', table: '镜头表', animatic: '时间预演' };

function groupIdOf(group: RecordValue): string {
  return String(group.group_id ?? group.id ?? '');
}

function groupTitleOf(group: RecordValue, groupId: string): string {
  return String(group.title ?? group.label ?? group.name ?? groupId);
}

function groupPurposeOf(group: RecordValue): string {
  return String(group.objective ?? group.narrative_purpose ?? group.purpose ?? '未填写叙事目的');
}

export const NarrativeGroupStoryboardNode = memo(function NarrativeGroupStoryboardNode({ id, data }: NodeProps) {
  const d = data as ProductionContentNodeData;
  const project = readUrl().project ?? '';
  const rawEpisodeNumber = d.episodeNumber;
  const hasValidEpisode = typeof rawEpisodeNumber === 'number' && Number.isInteger(rawEpisodeNumber) && rawEpisodeNumber > 0;
  const episode = hasValidEpisode ? rawEpisodeNumber : 0;
  const revisionId = typeof d.directorRevisionId === 'string' ? d.directorRevisionId : '';
  const boundGroupId = typeof d.groupId === 'string' ? d.groupId : '';
  const updateNodeData = useCanvasStore(state => state.updateNodeData);
  const client = useQueryClient();
  const [view, setView] = useState<StoryboardView>('board');
  const revisionQuery = useQuery({
    queryKey: ['director-plan', project, episode, revisionId],
    queryFn: () => {
      if (!hasValidEpisode) throw new Error('此组分镜节点缺少有效集数绑定。');
      return apiCall<RecordValue>(`projects/${encodeURIComponent(project)}/episodes/${episode}/director-plans/${encodeURIComponent(revisionId)}`);
    },
    enabled: Boolean(project && hasValidEpisode && revisionId),
    retry: false,
  });
  const revision = revisionQuery.data;
  const revisionIdentityMatches = Boolean(revision && String(revision.revision_id ?? '') === revisionId);
  const workspaceQuery = useQuery({
    queryKey: ['director-workspace', project, episode],
    queryFn: () => directorWorkspaceApi.get(project, episode),
    enabled: Boolean(project && hasValidEpisode && revisionIdentityMatches),
  });
  const planGroups = revisionIdentityMatches && Array.isArray(revision?.groups) ? revision.groups as RecordValue[] : [];
  const group = planGroups.find(item => groupIdOf(item) === boundGroupId);
  const shots = Array.isArray(group?.shots) ? group.shots as RecordValue[] : [];
  const mediaQuery = useQuery({
    queryKey: ['director-preview-media', project, episode, revisionId],
    queryFn: () => apiCall<DirectorPreviewGroup[]>(`projects/${encodeURIComponent(project)}/episodes/${episode}/narrative-groups`),
    enabled: Boolean(project && hasValidEpisode && revisionId && revision?.status === 'active'),
  });
  const mediaRows = Array.isArray(mediaQuery.data) ? mediaQuery.data : [];
  const mediaGroups = mediaRows.filter(item => item.director_revision_id === revisionId && item.id === boundGroupId);
  const media = mediaGroups[0];
  const boardUrl = media?.stages.render?.grid_asset && !media.stages.render.needs_regeneration
    ? resolveMediaUrl(media.stages.render.grid_asset) : null;
  const groupName = group ? groupTitleOf(group, boundGroupId) : boundGroupId || '叙事组';
  const purpose = group ? groupPurposeOf(group) : '';
  const totalSeconds = shots.reduce((total, shot) => total + Math.max(0, Number(shot.duration_seconds) || 0), 0);
  const versionLabel = revision?.status === 'active' ? '已采用版本' : '候选版本';
  const episodeLabel = hasValidEpisode ? `E${String(episode).padStart(2, '0')}` : '未绑定集数';
  const renderStatus = media?.stages.render?.status ?? (mediaQuery.error ? '读取失败' : '无该版本媒体');
  const videoStatus = media?.stages.video?.status ?? (mediaQuery.error ? '读取失败' : '无该版本媒体');
  const sceneId = String((group?.creative_context as RecordValue | undefined)?.scene_id ?? group?.scene_id ?? group?.scene_anchor ?? '');
  const previewSemantics = workspaceQuery.data?.semantics?.candidates.find(candidate => candidate.revision_id === revision?.semantic_revision_id);
  const sceneBlockings = workspaceQuery.data?.scene_blocking as Record<string, { current?: { blocking?: import('../production/SceneBlockingEditor').SceneBlocking } }> | undefined;
  const title = typeof d.displayName === 'string' && d.displayName.trim()
    ? d.displayName.trim()
    : `${episodeLabel} · ${groupName} · 组分镜`;
  const summary = revisionIdentityMatches && group
    ? `${purpose} · ${shots.length} 镜头 · ${totalSeconds.toFixed(1)} 秒 · ${versionLabel} ${revisionId} · 分镜 ${renderStatus} / 视频 ${videoStatus}`
    : `绑定 ${episodeLabel} · ${boundGroupId || '未指定叙事组'} · 导演版本 ${revisionId || '未指定'}`;

  const setCandidateRevision = (candidate: RecordValue) => {
    const candidateId = typeof candidate.revision_id === 'string' ? candidate.revision_id : '';
    if (!candidateId) return;
    const candidateGroups = Array.isArray(candidate.groups) ? candidate.groups as RecordValue[] : [];
    if (!candidateGroups.some(item => groupIdOf(item) === boundGroupId)) return;
    const revisionsKey = ['director-plan-revisions', project, episode];
    client.setQueryData<RecordValue[]>(revisionsKey, previous => [
      ...(previous ?? []).filter(item => item.revision_id !== candidateId),
      candidate,
    ]);
    updateNodeData(id, { directorRevisionId: candidateId });
  };

  const connectToGrid = () => {
    if (!group || !revisionId || !boundGroupId || !project || !hasValidEpisode) return;
    const shotDescriptions = Object.fromEntries(shots.map((shot, index) => {
      const shotId = String(shot.id ?? index + 1);
      return [shotId, String(shot.visual_description ?? shot.description ?? shot.action ?? shot.subject ?? '')];
    }));
    const binding: GroupGridBinding = {
      project,
      episode,
      groupId: boundGroupId,
      directorRevisionId: revisionId,
      shotIds: shots.map((shot, index) => String(shot.id ?? index + 1)),
      shotDescriptions,
    };
    try {
      validateGridBinding(binding);
      connectNarrativeGroupGrid({
        sourceNodeId: id,
        binding,
        displayName: `${episodeLabel} · ${groupName} · 宫格出图`,
        prompt: shots.map((shot, index) => `${index + 1}. ${String(shot.id ?? index + 1)} ${String(shot.shot_size ?? shot.framing ?? '')}\n${shotDescriptions[String(shot.id ?? index + 1)]}`).join('\n\n'),
        aspectRatio: String((group.layout as RecordValue | undefined)?.aspect_ratio ?? group.aspect_ratio ?? '9:16'),
      });
    } catch (error) {
      updateNodeData(id, { connectionError: error instanceof Error ? error.message : '无法连接宫格出图节点' });
    }
  };

  let boundError = '';
  if (!project || !revisionId || !boundGroupId) {
    boundError = '此组分镜节点缺少项目、叙事组或导演版本绑定，请从导演工作空间重新创建。';
  } else if (!hasValidEpisode) {
    boundError = '此组分镜节点缺少有效集数绑定，请从导演工作空间重新创建。';
  } else if (revisionQuery.error) {
    boundError = `无法读取绑定导演版本 ${revisionId}：${String(revisionQuery.error)}`;
  } else if (revisionQuery.isSuccess && !revision) {
    boundError = `服务未返回绑定导演版本 ${revisionId}。`;
  } else if (revision && !revisionIdentityMatches) {
    boundError = `绑定导演版本 ${revisionId} 与服务返回的版本 ${String(revision.revision_id ?? '未知')} 不一致。`;
  } else if (revision && !group) {
    boundError = `导演版本 ${revisionId} 中找不到叙事组 ${boundGroupId}。`;
  }

  return <ProductionNodeShell title={title} summary={summary}>
    <div className="space-y-4">
      {boundError && <p role="alert" className="text-sm text-destructive">{boundError}</p>}
      {!boundError && revisionQuery.isPending && <p role="status" className="text-sm text-muted-foreground">正在读取绑定导演版本 {revisionId}…</p>}
      {!boundError && mediaQuery.error && <p role="alert" className="text-sm text-destructive">无法读取此版本的组媒体状态：{String(mediaQuery.error)}</p>}
      {!boundError && group && <>
        <header className="space-y-1 rounded-lg border p-3">
          <h3 className="font-medium">{groupName}</h3>
          <p className="text-sm text-muted-foreground">{purpose}</p>
          <p className="text-xs text-muted-foreground">{shots.length} 个镜头 · 目标时长 {totalSeconds.toFixed(1)} 秒 · {versionLabel} {revisionId}</p>
          <p className="text-xs text-muted-foreground">分镜：{renderStatus} · 视频：{videoStatus}</p>
        </header>
        <div className="flex flex-wrap gap-2" role="group" aria-label="组分镜查看方式">
          {(Object.keys(viewLabels) as StoryboardView[]).map(mode => <Button key={mode} variant={view === mode ? 'default' : 'outline'} onClick={() => setView(mode)}>{viewLabels[mode]}</Button>)}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-emerald-300/15 bg-emerald-300/[0.04] p-3">
          <div><p className="font-medium">出图在独立宫格节点中执行</p><p className="text-xs text-muted-foreground">按此导演版本、叙事组和镜头顺序打开或复用画布节点，不会自动提交任务。</p></div>
          <Button variant="outline" onClick={connectToGrid}>连接至宫格出图</Button>
        </div>
        {typeof d.connectionError === 'string' && <p role="alert" className="text-sm text-destructive">{d.connectionError}</p>}
        {view === 'animatic' && <p className="text-sm text-muted-foreground">时间预演只使用此版本已有图像和音轨；缺失素材以文字占位，不生成媒体。</p>}
        {view !== 'board' && <DirectorShotViews groups={[{ ...group, scene_id: sceneId }]} view={view} semantics={previewSemantics} sceneBlockings={sceneBlockings} mediaGroups={mediaGroups} />}
        {view === 'board' && <section aria-label="组内镜头图板" className="space-y-3">
          {boardUrl
            ? <img className="max-h-80 w-full rounded-lg border object-contain" src={boardUrl} alt={`${groupName} 已有分镜图`} />
            : <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">当前版本没有可用分镜图，以下内容以文字占位显示。</p>}
          <DirectorGroupStructureEditor project={project} episode={episode} revisionId={revisionId} groups={planGroups} group={group} onSaved={setCandidateRevision} />
          {shots.map((shot, index) => {
            const shotId = String(shot.id ?? index + 1);
            return <article key={shotId} className="rounded-lg border bg-muted/20 p-3 text-sm">
              <h4 className="font-medium">{index + 1}. {String(shot.shot_size ?? shot.framing ?? '镜头')} · {shotId}</h4>
              <p className="mt-1">{String(shot.subject ?? shot.description ?? shot.visual_description ?? '暂无画面摘要')}</p>
              <p className="mt-1">动作：{String(shot.action ?? '未填写')}</p>
              <p className="mt-1">对白／声音：{String(shot.dialogue ?? shot.sound ?? shot.audio_intent ?? '无明确标注')}</p>
              <p className="mt-1 text-xs text-muted-foreground">{Number(shot.duration_seconds ?? 0) || '未设'} 秒 · 来源场景 {String(shot.scene_id ?? group.scene_anchor ?? '未标注')}</p>
              <DirectorShotEditor key={`${revisionId}:${shotId}`} project={project} episode={episode} revisionId={revisionId} shot={shot} group={group} shotIndex={index} media={media} onSaved={setCandidateRevision} />
            </article>;
          })}
          {!shots.length && <p className="text-sm text-muted-foreground">此叙事组当前没有镜头。</p>}
        </section>}
      </>}
    </div>
  </ProductionNodeShell>;
});
