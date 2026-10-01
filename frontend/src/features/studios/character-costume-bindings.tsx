import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import { jsonWithBackendError } from "@/lib/api-errors";
import { Button } from "@/components/ui/button";
import { queryKeys } from "@/lib/query-keys";
type Target = { episode: number; title: string; current_identity_id?: string; expected_episode_digest: string };

export function CharacterCostumeBindings({ project, name, identityId }: { project: string; name: string; identityId: string }) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<number[]>([]);
  const [message, setMessage] = useState("");
  const targets = useQuery({ queryKey: ["studio-costume-targets", project, name], queryFn: () => jsonWithBackendError<{ data: Target[] }>(api.get(p`api/v1/projects/${project}/studios/characters/${name}/costume-targets`)) });
  const apply = useMutation({ mutationFn: async () => {
    const results: string[] = [];
    for (const target of targets.data?.data.filter((item) => selected.includes(item.episode)) ?? []) {
      await jsonWithBackendError(api.post(p`api/v1/projects/${project}/studios/characters/${name}/apply-costume`, { json: { episode: target.episode, identity_id: identityId, expected_episode_digest: target.expected_episode_digest } }));
      results.push(`第 ${target.episode} 集`);
    }
    return results;
  }, onSuccess: (results) => { setMessage(`${results.join("、")}的后续角色引用已更新，已有素材与运行中任务保持原版本。`); void targets.refetch(); void queryClient.invalidateQueries({ queryKey: queryKeys.episodes(project) }); void queryClient.invalidateQueries({ queryKey: ["character-studio-impact", project, name] }); }, onError: (error) => { setMessage(`更新停止，已完成的集保留：${error.message}`); void targets.refetch(); } });
  return <div className="space-y-2 rounded border p-3"><h4 className="text-sm font-medium">将此服装应用到指定剧集的后续引用</h4><p className="text-xs text-muted-foreground">只修改勾选剧集的默认服装与规划引用，不重生成素材。导演方案明确指定的其他身份仍需单独审阅。</p>{targets.data?.data.map((target) => <label className="flex gap-2 text-sm" key={target.episode}><input type="checkbox" checked={selected.includes(target.episode)} onChange={(event) => setSelected((items) => event.target.checked ? [...items, target.episode] : items.filter((item) => item !== target.episode))} />第 {target.episode} 集 · {target.title}{target.current_identity_id === identityId ? "（已使用此服装）" : ""}</label>)}<Button variant="outline" disabled={!selected.length || apply.isPending} onClick={() => apply.mutate()}>更新勾选剧集的后续引用</Button>{message && <p role="status" className="text-sm">{message}</p>}{targets.error && <p role="alert">{targets.error.message}</p>}</div>;
}
