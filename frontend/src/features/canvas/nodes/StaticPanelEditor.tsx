import { useId, useState } from 'react';
import type { StaticStoryboardPanel, StoryboardGenFrameItem } from '../domain/canvasNodes';

export function StaticPanelEditor({ frame, onAdopt, references = [] }: {
  frame: StoryboardGenFrameItem;
  onAdopt: (frame: StoryboardGenFrameItem) => void;
  references?: Array<{ node_id: string; entity_id: string; name: string }>;
}) {
  const prefix = useId();
  const [draft, setDraft] = useState<StaticStoryboardPanel>(() => frame.static_panel ?? {
    id: frame.id, shot_id: frame.id, anchor: 'start', state: '', framing: '',
    subjects: [], setting: '', lighting: '', visible_text: [],
  });
  const valid = [draft.state, draft.framing, draft.setting, draft.lighting].every(x => x.trim())
    && draft.subjects.every(s => Object.values(s).every(x => x.trim()));
  return <details className="nodrag nowheel mt-2 rounded border border-white/15 p-2">
    <summary>分镜画面 · {frame.static_panel ? '已设定' : '需要设定选定瞬间'}</summary>
    <p className="my-2 text-text-muted">每格只表现一个机位、一个瞬间；完整动作保留在导演设计中。</p>
    <label className="block">选定瞬间
      <select value={draft.anchor} onChange={e => setDraft({ ...draft, anchor: e.target.value as StaticStoryboardPanel['anchor'] })}>
        <option value="start">开始</option><option value="end">结束</option><option value="selected">指定瞬间</option>
      </select>
    </label>
    {([['state', '当前可见状态'], ['framing', '单一机位与构图'], ['setting', '场景'], ['lighting', '当前光线']] as const).map(([key, title]) =>
      <label key={key} htmlFor={`${prefix}-${key}`} className="my-2 block">{title}
        <textarea id={`${prefix}-${key}`} value={draft[key]} className="block w-full rounded bg-black/20 p-2"
          onChange={e => setDraft({ ...draft, [key]: e.target.value })} />
      </label>)}
    <label className="block">画面内文字（每行一项，无则留空）
      <textarea value={draft.visible_text.join('\n')} className="block w-full bg-black/20 p-2"
        onChange={e => setDraft({ ...draft, visible_text: e.target.value.split('\n').filter(Boolean) })} />
    </label>
    {draft.subjects.map((subject, index) => <fieldset key={index} className="my-2 border border-white/15 p-2">
      <legend>出场人物 {index + 1}</legend>
      <label className="block">人物基础形象<select value={subject.appearance_reference} onChange={e => {
        const reference = references.find(r => r.node_id === e.target.value);
        if (reference) setDraft({ ...draft, subjects: draft.subjects.map((s, i) => i === index
          ? { ...s, entity_id: reference.entity_id, appearance_reference: reference.node_id } : s) });
      }}><option value="">请选择已连接的基础形象</option>
        {references.map(r => <option key={r.node_id} value={r.node_id}>{r.name}</option>)}
      </select></label>
      {([['screen_position', '画面位置'], ['pose', '姿态与动作归属'], ['gaze', '视线目标']] as const).map(([key, label]) =>
        <label key={key} className="block">{label}<input value={subject[key]} className="block w-full bg-black/20 p-1"
          onChange={e => setDraft({ ...draft, subjects: draft.subjects.map((s, i) => i === index ? { ...s, [key]: e.target.value } : s) })} /></label>)}
      <button type="button" onClick={() => setDraft({ ...draft, subjects: draft.subjects.filter((_, i) => i !== index) })}>移除此人物</button>
    </fieldset>)}
    {references.length === 0 && <p>需要人物时，请先连接人物基础形象节点。</p>}
    <button type="button" disabled={references.length === 0} onClick={() => setDraft({ ...draft, subjects: [...draft.subjects,
      { entity_id: '', appearance_reference: '', screen_position: '', pose: '', gaze: '' }] })}>添加出场人物</button>
    <button type="button" disabled={!valid} className="ml-3 disabled:opacity-40" onClick={() => onAdopt({ ...frame,
      static_panel: draft, static_source_description: frame.description })}>采用此画面</button>
  </details>;
}
