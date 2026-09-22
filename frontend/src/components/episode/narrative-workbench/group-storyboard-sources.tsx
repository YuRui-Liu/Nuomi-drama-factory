// SPDX-License-Identifier: Elastic-2.0
import { HTTPError } from "ky";
import { Button } from "@/components/ui/button";
import { BackendStatusError } from "@/lib/api-errors";
import { useSelectStoryboardSource, useStoryboardSources } from "@/lib/queries/narrative-groups";

export function GroupStoryboardSources({ project, episode, groupId }: {
  project: string; episode: number; groupId: string;
}) {
  const query = useStoryboardSources(project, episode, groupId);
  const selection = useSelectStoryboardSource(project, episode, groupId);
  const data = query.data?.ok ? query.data.data : undefined;
  const conflict = (selection.error instanceof BackendStatusError && selection.error.status === 409)
    || (selection.error instanceof HTTPError && selection.error.response.status === 409);
  return <section aria-label="分镜来源" className="mt-3 space-y-3 rounded-lg border p-3">
    <div className="flex items-center justify-between gap-3">
      <h4 className="text-sm font-medium">分镜来源</h4>
      <Button size="sm" variant="outline" disabled={query.isFetching || selection.isPending}
        onClick={() => { selection.reset(); void query.refetch(); }}>刷新来源</Button>
    </div>
    <p className="text-xs text-muted-foreground">选用只切换分镜来源，不会生成或购买素材；依赖旧来源的视频需重新生成。</p>
    {query.isPending && <p>正在校验来源…</p>}
    {(query.isError || query.data?.ok === false) && <p role="alert">来源查询失败，请刷新后重试。</p>}
    {selection.isError && <p role="alert">{conflict ? "来源已变化，请刷新后重新选择" : "来源选择失败，请检查素材后重试"}</p>}
    {!query.isError && data?.items.map((source, index) => {
      const label = source.asset_id || source.source_id || `无效来源 ${index + 1}`;
      const selected = data.selected_storyboard_id === source.source_id
        || Object.values(data.selected_storyboard_sources).includes(source.source_id);
      return <article key={`${source.source_id}-${index}`} className="rounded border p-2">
        <p className="break-all text-xs">{label}</p>
        {source.validation.valid && source.grid_url && <img className="my-2 max-h-48 object-contain"
          src={source.grid_url} alt={`分镜来源 ${label}`} />}
        {!source.validation.valid && <p className="text-xs text-destructive">素材校验失败，不可选用</p>}
        <Button size="sm" variant="outline" aria-label={`${selected ? "当前选中" : "选用"} ${label}`}
          disabled={selected || !source.validation.valid || selection.isPending || conflict || query.isFetching}
          onClick={() => selection.mutate({ sourceId: source.source_id, expectedSelectedId: data.selected_storyboard_id })}>
          {selected ? "当前选中" : "选用此来源"}
        </Button>
      </article>;
    })}
    {data?.items.length === 0 && <p className="text-xs">暂无带来源记录的分镜。</p>}
  </section>;
}
