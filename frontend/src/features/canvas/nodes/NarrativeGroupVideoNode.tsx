import { memo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { NodeProps } from '@xyflow/react';
import { apiCall } from '@/api/client';
import { Button } from '@/components/ui/button';
import { GroupVideoReferenceDialog } from '@/components/episode/narrative-workbench/group-video-reference-dialog';
import { useNarrativeGroupVideoReferencePreview, type VideoReferencePreview } from '@/lib/queries/narrative-groups';
import { resolveMediaUrl } from '@/lib/media-url';
import { readUrl } from '@/lib/url-params';
import { useCanvasStore } from '@/stores/canvasStore';
import { canEditEpisodeNode, useWorkspaceScope } from '@/features/freezone/WorkspaceScope';
import type { ProductionContentNodeData } from '../domain/canvasNodes';
import { ProductionGraphNodeCard } from './ProductionGraphNodeCard';
import { ProductionVideoPreview } from './ProductionVideoPreview';
import { GroupVideoKeyframes } from './GroupVideoKeyframes';

export const NarrativeGroupVideoNode = memo(function NarrativeGroupVideoNode({ id, data }: NodeProps) {
  const d = data as ProductionContentNodeData;
  const updateNodeData = useCanvasStore(state => state.updateNodeData);
  const scope = useWorkspaceScope();
  const project = readUrl().project ?? '';
  const episode = d.episodeNumber;
  const groupId = typeof d.groupId === 'string' ? d.groupId : '';
  const revisionId = typeof d.directorRevisionId === 'string' ? d.directorRevisionId : '';
  const validEpisode = typeof episode === 'number' && Number.isInteger(episode) && episode > 0;
  const validationError = !project || !validEpisode || !groupId || !revisionId
    ? '此制作节点缺少项目、集数、叙事组或固定导演版本。请从已采用导演方案重新创建制作图。'
    : undefined;
  const title = typeof d.displayName === 'string' && d.displayName.trim()
    ? d.displayName.trim()
    : `E${String(validEpisode ? episode : 0).padStart(2, '0')} · ${groupId || '叙事组'} · 整组视频`;
  const videoUrl = typeof d.videoUrl === 'string' && d.videoUrl ? resolveMediaUrl(d.videoUrl) : null;
  const dialogueSource = typeof d.dialogueSource === 'string' ? d.dialogueSource : '';
  const busy = ['running', 'submission_unknown'].includes(String(d.orchestration?.status ?? ''));
  const [referencesOpen, setReferencesOpen] = useState(false);
  const canEditReferences = canEditEpisodeNode(scope, episode) && scope?.projectId === project;
  const revisionQuery = useQuery({
    queryKey: ['director-plan', project, episode, revisionId],
    queryFn: () => apiCall<Record<string, unknown>>(`projects/${encodeURIComponent(project)}/episodes/${episode}/director-plans/${encodeURIComponent(revisionId)}`),
    enabled: Boolean(project && validEpisode && revisionId && canEditReferences),
    retry: false,
  });
  const revision = revisionQuery.data;
  const revisionContainsGroup = Array.isArray(revision?.groups)
    && (revision.groups as Array<Record<string, unknown>>).some(item => String(item.group_id ?? item.id ?? '') === groupId);
  const revisionIsActive = Boolean(revision && String(revision.revision_id ?? '') === revisionId
    && revision.status === 'active' && revisionContainsGroup);
  const referenceQuery = useNarrativeGroupVideoReferencePreview(
    project,
    validEpisode ? episode as number : 0,
    groupId,
    revisionIsActive,
  );
  const referencePreview = referenceQuery.data?.ok ? referenceQuery.data.data : null;

  return <ProductionGraphNodeCard
    title={title}
    summary={`叙事组 ${groupId || '未绑定'} · 固定版本视频制作`}
    validationError={validationError}
    orchestration={d.orchestration}
    result={<>
      <label className="nodrag mb-3 grid gap-1 text-xs">
        对白与声音来源
        <select aria-label="组视频声音来源" className="rounded-md border border-border bg-background p-2"
          value={dialogueSource} disabled={busy || Boolean(validationError) || !revisionIsActive || !canEditEpisodeNode(scope, episode) || scope?.projectId !== project}
          onChange={event => updateNodeData(id, { dialogueSource: event.target.value || null })}>
          <option value="">沿用分镜声音设置</option>
          <option value="h3_native">MiniMax H3 原生音画</option>
          <option value="external_tts">独立配音轨</option>
        </select>
        <span className="text-muted-foreground">原生音画随组视频生成对白和环境声；独立配音需已完成的配音轨。修改后需重新运行此组。</span>
      </label>
      {revision && !revisionIsActive ? <p role="status" className="mb-3 text-xs text-amber-700">{String(revision.revision_id ?? '') === revisionId && revision.status === 'active' ? `固定版本中找不到叙事组 ${groupId}` : '此导演版本已过期或尚未采用'}，视频引用配置为只读。</p> : null}
      {revisionQuery.error ? <p role="alert" className="mb-3 text-xs text-destructive">无法确认固定导演版本状态：{String(revisionQuery.error)}</p> : null}
      <div className="nodrag mb-3 space-y-1">
        <Button type="button" variant="outline" disabled={Boolean(validationError) || !revisionIsActive || !canEditReferences || busy} onClick={() => setReferencesOpen(true)}>管理组视频引用</Button>
        <p className="text-xs text-muted-foreground">保存当前组的视频参考图和描述；视频任务仍由此节点的画布运行面板提交。</p>
      </div>
      {videoUrl
      ? <ProductionVideoPreview src={videoUrl} title={title} />
      : <p className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">运行完成后，组视频会显示在此节点。</p>}
      {!validationError && <GroupVideoKeyframes project={project} episode={episode as number} groupId={groupId} revision={revisionId} />}
      <GroupVideoReferenceDialog
        open={referencesOpen}
        onOpenChange={setReferencesOpen}
        project={project}
        episode={validEpisode ? episode as number : 0}
        groupId={groupId}
        expectedDirectorRevisionId={revisionId}
        preview={referencePreview as VideoReferencePreview | null}
        loading={referenceQuery.isFetching || revisionQuery.isFetching}
        error={referenceQuery.error instanceof Error ? referenceQuery.error : referenceQuery.data?.ok === false ? new Error(referenceQuery.data.error) : null}
        minImages={0}
        maxImages={referencePreview?.max_images ?? 0}
        onRefresh={async () => {
          const refreshed = await referenceQuery.refetch();
          return refreshed.data?.ok ? refreshed.data.data : undefined;
        }}
      />
    </>}
  />;
});
