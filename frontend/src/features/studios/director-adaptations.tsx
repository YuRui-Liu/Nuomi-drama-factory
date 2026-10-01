import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import { jsonWithBackendError } from "@/lib/api-errors";
import { listStudioDocuments, type StudioDocument } from "./studio-api";
type Adaptation = { purpose?: string; episode: number; source_revision: number; status: string; accepted_source_ids: string[]; suggestions: Array<{ source_span_id: string; original: string; proposed: string; reason: string }> };

export function DirectorAdaptations({ project, episode }: { project: string; episode: number }) {
  const docs = useQuery({ queryKey: ["studio-adaptations", project], queryFn: () => listStudioDocuments<Adaptation>(project, "director"), refetchInterval: 5000 });
  return <section className="space-y-3 rounded-xl border p-4"><h3 className="font-medium">故事与对白改编 · 单独审阅</h3><p className="text-xs text-muted-foreground">这些建议独立于正常导演方案。采纳会保存新的改编草稿版本，不自动改写原剧本或重新生成视频。可复制已采纳文本到剧本工作区继续编辑。</p>{docs.data?.filter((doc) => doc.data.purpose === "director-adaptation" && doc.data.episode === episode).map((doc) => <AdaptationCard key={`${doc.id}/${doc.revision}`} project={project} doc={doc} refresh={() => void docs.refetch()} />)}{docs.error && <p role="alert">建议读取失败：{docs.error.message}</p>}</section>;
}

function AdaptationCard({ project, doc, refresh }: { project: string; doc: StudioDocument<Adaptation>; refresh: () => void }) {
  const [selected, setSelected] = useState<string[]>(doc.data.accepted_source_ids ?? []);
  const adopt = useMutation({ mutationFn: () => jsonWithBackendError(api.post(p`api/v1/projects/${project}/studios/director/adaptations/${doc.id}/adopt`, { json: { expected_revision: doc.revision, source_ids: selected } })), onSuccess: refresh });
  return <article className="space-y-3 rounded border p-3"><h4>第 {doc.data.episode} 集 · 原文 v{doc.data.source_revision} · 建议 v{doc.revision} · {doc.data.status}</h4>{doc.data.suggestions.map((item) => <label className="block space-y-1 border-t pt-2 text-sm" key={item.source_span_id}><span className="flex gap-2"><input type="checkbox" checked={selected.includes(item.source_span_id)} onChange={(event) => setSelected((values) => event.target.checked ? [...values, item.source_span_id] : values.filter((value) => value !== item.source_span_id))} />{item.source_span_id}</span><p>原文：{item.original}</p><p>建议：{item.proposed}</p><p className="text-muted-foreground">理由：{item.reason}</p></label>)}{!doc.data.suggestions.length && <p>此轮未提出需要改编的内容。</p>}<Button disabled={!selected.length || adopt.isPending} onClick={() => adopt.mutate()}>采纳所选为改编草稿版本</Button>{adopt.error && <p role="alert">{adopt.error.message}</p>}</article>;
}
