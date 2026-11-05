import { useQuery } from '@tanstack/react-query';
import { apiCall } from '@/api/client';
import { resolveMediaUrl } from '@/lib/media-url';

interface Frame { path: string; sha256: string }
export interface GroupKeyframes {
  director_revision_id: string;
  revision: number;
  stale: boolean;
  shot_ids: string[];
  bindings: Array<{ shot_id: string; first_frame?: Frame; last_frame?: Frame }>;
}

export function GroupVideoKeyframeStatus({ data, revision, project }: { data: GroupKeyframes; revision: string; project: string }) {
  if (data.stale || data.director_revision_id !== revision) {
    return <p role="status" className="text-xs text-amber-500">首尾帧绑定属于旧导演版本，请核对。</p>;
  }
  const complete = data.shot_ids.filter(id => data.bindings.some(b => b.shot_id === id && b.first_frame?.path && b.first_frame.sha256 && b.last_frame?.path && b.last_frame.sha256)).length;
  const imageUrl = (path: string) => resolveMediaUrl(`/static/projects/${encodeURIComponent(project)}/${path}`) ?? undefined;
  return <details className="nodrag nowheel mt-3 rounded border p-2 text-xs">
    <summary>实际视频首尾帧 · 已绑定 {complete}/{data.shot_ids.length} 镜</summary>
    <p className="my-2 text-muted-foreground">来自组视频绑定 v{data.revision}；旧联系表的帧模式不代表此处状态。</p>
    {data.shot_ids.map(id => {
      const row = data.bindings.find(b => b.shot_id === id);
      return <div key={id} className="mb-2"><span>{id}</span><div className="grid grid-cols-2 gap-2">
        {(['first_frame', 'last_frame'] as const).map((key, i) => <div key={key}>
          <p>{i === 0 ? '首帧' : '尾帧'}</p>
          {row?.[key]?.path ? <img className="w-full rounded" src={imageUrl(row[key]!.path)} alt={`${id} ${i === 0 ? '实际首帧' : '实际尾帧'}`} /> : <p>未绑定</p>}
        </div>)}
      </div></div>;
    })}
  </details>;
}

export function GroupVideoKeyframes({ project, episode, groupId, revision }: { project: string; episode: number; groupId: string; revision: string }) {
  const query = useQuery({
    queryKey: ['group-video-keyframes', project, episode, groupId, revision],
    queryFn: () => apiCall<GroupKeyframes>(`projects/${encodeURIComponent(project)}/episodes/${episode}/narrative-groups/${encodeURIComponent(groupId)}/video/keyframes`),
    enabled: Boolean(project && episode > 0 && groupId && revision),
    staleTime: 30_000,
    retry: false,
  });
  if (query.error) return <p role="status" className="text-xs text-amber-500">无法读取实际首尾帧绑定，请刷新核对。</p>;
  if (!query.data) return <p className="text-xs text-muted-foreground">正在核对实际视频首尾帧…</p>;
  return <GroupVideoKeyframeStatus data={query.data} revision={revision} project={project} />;
}
