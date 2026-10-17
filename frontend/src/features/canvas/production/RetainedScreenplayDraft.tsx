import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { scriptCreationApi } from '@/features/script-creation/api';
import type { ScriptDocument } from '@/features/script-creation/types';
import { useProductionAction } from './useProductionAction';
import { creativeWorkflowApi } from './creativeWorkflowApi';

export function RetainedScreenplayDraft({ project, runId, episode, onCreated }: {
  project: string; runId: string; episode: number; onCreated: (document: ScriptDocument) => Promise<void>;
}) {
  const { busy, error, perform } = useProductionAction();
  const [selected, setSelected] = useState<number | null>(null);
  const report = useQuery({ queryKey: ['retained-screenplay', project, runId], queryFn: () => scriptCreationApi.getGenerationCraft(project, runId) });
  const drafts = report.data?.records.filter(record => record.step_key === `episode_script:${episode}` && typeof record.output.markdown === 'string') ?? [];
  const markdown = drafts[selected ?? drafts.length - 1]?.output.markdown;
  return <section className="space-y-3 rounded border p-4"><h3 className="font-medium">保留的待修剧本</h3><p className="text-sm">将最后草稿保存为可编辑正文，按审稿意见修改后重新提交独立审稿。此操作不会采用剧本或启动导演任务。</p>
    {(error || report.error) && <p role="alert">{error || String(report.error)}</p>}
    {drafts.length > 0 && <><label className="block">选择保留草稿<select aria-label="选择保留草稿" className="ml-2 rounded border p-2" value={selected ?? drafts.length - 1} onChange={event => setSelected(Number(event.target.value))}>{drafts.map((record, index) => <option value={index} key={`${record.stage}:${index}`}>{index + 1} · {record.stage === 'draft' ? '初稿' : '修订稿'}</option>)}</select></label><details><summary>查看选定草稿</summary><pre className="max-h-80 overflow-auto whitespace-pre-wrap text-sm">{markdown}</pre></details></>}
    <Button variant="outline" disabled={busy || !markdown} onClick={() => void perform(`recover:${runId}:${selected ?? drafts.length - 1}`, async mutation => {
      const result = await creativeWorkflowApi.recoverScreenplay(project, runId, selected ?? drafts.length - 1, mutation);
      await onCreated(result.document);
    })}>将草稿载入正文编辑</Button>
  </section>;
}
