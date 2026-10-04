import { useState } from 'react';
import { Copy } from 'lucide-react';
import type { CasePrompt } from '@/api/techniqueLibrary';

const labels: Record<string, string> = { author: '作者公开原文', official: '官方公开原文', reconstructed: '反推重构', unknown: '来源类别待确认' };

export function CasePromptPanel({ prompts = [] }: { prompts?: CasePrompt[] }) {
  const [selected, setSelected] = useState(0);
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const prompt = prompts[selected] ?? prompts[0];
  if (!prompt) return <p className="text-sm text-slate-400">此案例暂未收录原提示词，请查看原始出处。</p>;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(prompt.text);
      setCopyState('copied');
    } catch { setCopyState('failed'); }
  };
  return <section aria-label="案例原提示词" className="space-y-3 rounded-xl border border-white/15 p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h4>原提示词</h4>
      <button type="button" className="flex items-center gap-2 rounded border border-white/20 px-3 py-2 text-xs" onClick={() => void copy()}><Copy size={14} aria-hidden="true" />复制完整提示词</button>
    </div>
    {prompts.length > 1 && <select aria-label="提示词来源" className="w-full rounded bg-slate-800 p-2 text-xs" value={selected} onChange={(event) => { setSelected(Number(event.target.value)); setCopyState('idle'); }}>
      {prompts.map((record, index) => <option key={`${record.repository}:${record.path}:${index}`} value={index}>{record.repository} · {labels[record.provenance] ?? labels.unknown} · {record.language}</option>)}
    </select>}
    <p className="text-xs text-amber-200">{labels[prompt.provenance] ?? labels.unknown}</p>
    <p className="break-all text-xs text-slate-400">{prompt.repository} · {prompt.path} · {prompt.revision}</p>
    <pre className="max-h-96 overflow-y-auto whitespace-pre-wrap break-words font-sans text-sm leading-7 text-slate-200">{prompt.text}</pre>
    {copyState === 'copied' && <p role="status" className="text-xs text-emerald-300">已复制</p>}
    {copyState === 'failed' && <p role="alert" className="text-xs text-amber-200">复制失败，请手动选择原文复制。</p>}
  </section>;
}
