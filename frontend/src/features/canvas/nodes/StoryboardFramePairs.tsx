import { useState } from 'react';
import { apiCall } from '@/api/client';
import { readUrl } from '@/lib/url-params';

export interface StoryboardFramePair {
  id: string;
  first_frame_id: string;
  last_frame_id: string;
  description: string;
  duration_seconds: number;
  selection_source?: 'manual' | 'ai';
  mode?: 'first' | 'first_last';
  recommendation?: { mode: 'first' | 'first_last'; reason: string };
}

export function StoryboardFramePairs({ frames, pairs, onChange }: {
  frames: Array<{ id: string; description: string; duration_seconds?: number; durationSeconds?: number }>;
  pairs: StoryboardFramePair[];
  onChange: (pairs: StoryboardFramePair[]) => void;
}) {
  const [suggestions, setSuggestions] = useState<{ source: string; pairs: StoryboardFramePair[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const source = JSON.stringify({ frames, pairs });
  const recommend = async () => {
    setBusy(true); setError('');
    try {
      const project = readUrl().project;
      if (!project) throw new Error('请先打开项目画布');
      if (!pairs.length && frames.some(frame => !(Number(frame.duration_seconds ?? frame.durationSeconds) > 0))) {
        throw new Error('请先填写每个镜头的时长，或手动添加视频片段后推荐');
      }
      const segments = pairs.length ? pairs : frames.map(frame => ({ id: frame.id,
        description: frame.description,
        duration_seconds: Number(frame.duration_seconds ?? frame.durationSeconds) }));
      const result = await apiCall<{ pairs: StoryboardFramePair[] }>(
        `projects/${encodeURIComponent(project)}/freezone/video-director/recommend-frames`,
        { method: 'post', timeout: 180_000, json: { frames, segments } });
      setSuggestions({ source, pairs: result.pairs });
    } catch (cause) { setError(cause instanceof Error ? cause.message : '推荐失败'); }
    finally { setBusy(false); }
  };
  const edit = (index: number, patch: Partial<StoryboardFramePair>) =>
    onChange(pairs.map((pair, i) => i === index ? { ...pair, ...patch, selection_source: 'manual' } : pair));
  return <details className="nodrag rounded border border-white/10 p-2 text-xs">
    <summary>视频帧模式 · {pairs.length} 段</summary>
    <p className="my-2 text-text-muted">AI 按片段推荐首帧或首尾帧，允许手动调整。分镜只作帧输入，人物基础形象单独作 Ref。</p>
    <button type="button" disabled={busy} onClick={recommend}>{busy ? '正在推荐…' : 'AI 推荐帧模式'}</button>
    {error && <p role="alert">{error}</p>}
    {suggestions && <div>
      {suggestions.pairs.map(pair => <p key={pair.id}>{pair.id}：{pair.last_frame_id ? '首尾帧' : '首帧'} · {pair.recommendation?.reason}</p>)}
      <button type="button" disabled={suggestions.source !== source} onClick={() => { onChange(suggestions.pairs); setSuggestions(null); }}>应用推荐（替换当前选择）</button>
      {suggestions.source !== source && <p>输入或手动选择已变化，请重新推荐。</p>}
    </div>}
    {pairs.map((pair, index) => <div key={pair.id} className="my-2 grid gap-2 rounded border border-white/10 p-2">
      <span>片段 {index + 1}</span>
      <label>模式<select aria-label={`片段 ${index + 1} 模式`} value={pair.mode ?? (pair.last_frame_id ? 'first_last' : 'first')}
        onChange={e => edit(index, { mode: e.target.value as 'first' | 'first_last', last_frame_id: e.target.value === 'first' ? '' : pair.last_frame_id })}>
        <option value="first">首帧</option><option value="first_last">首尾帧（需选择结束画格）</option>
      </select></label>
      {pair.recommendation && <p>AI 建议：{pair.recommendation.reason}{pair.selection_source === 'manual' ? ' · 已手动调整' : ''}</p>}
      {(['first_frame_id', 'last_frame_id'] as const).map((field) => <label key={field}>
        {field === 'first_frame_id' ? '首帧' : '尾帧'}
        <select aria-label={`片段 ${index + 1} ${field === 'first_frame_id' ? '首帧' : '尾帧'}`}
          value={pair[field]} onChange={e => edit(index, { [field]: e.target.value,
            ...(field === 'last_frame_id' ? { mode: e.target.value ? 'first_last' as const : 'first' as const } : {}) })} className="ml-2 bg-black/30">
          <option value="">{field === 'first_frame_id' ? '请选择起始画格' : '不使用尾帧（首帧模式）'}</option>
          {frames.map((frame, i) => <option key={frame.id} value={frame.id}>画格 {i + 1} · {frame.id}</option>)}
        </select>
      </label>)}
      <textarea aria-label={`片段 ${index + 1} 动作`} placeholder="两帧之间发生的动作与对白"
        value={pair.description} onChange={e => edit(index, { description: e.target.value })} className="bg-black/30 p-2" />
      <label>时长（秒）<input aria-label={`片段 ${index + 1} 时长`} type="number" min="0.1" step="0.1"
        value={pair.duration_seconds} onChange={e => edit(index, { duration_seconds: Number(e.target.value) })} className="ml-2 w-16 bg-black/30" /></label>
      <button type="button" onClick={() => onChange(pairs.filter((_, i) => i !== index))}>删除此配对</button>
    </div>)}
    <button type="button" onClick={() => onChange([...pairs, { id: crypto.randomUUID(), first_frame_id: '', last_frame_id: '', mode: 'first', selection_source: 'manual', description: '', duration_seconds: 4 }])}>
      添加视频片段
    </button>
  </details>;
}
